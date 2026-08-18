#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import signal
import ssl
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

REDACTED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "anthropic-api-key",
    "api-key",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: ("<redacted>" if key.lower() in REDACTED_HEADERS else value)
        for key, value in headers.items()
    }


def _decode_body(body: bytes) -> tuple[Any | None, str | None]:
    if not body:
        return None, None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        return None, text


def _normalize_message_content(content: Any) -> Any:
    if not isinstance(content, list) or not content:
        return content

    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            return content
        item_type = str(item.get("type") or "").strip().lower()
        text = item.get("text")
        if item_type not in {"output_text", "text"} or not isinstance(text, str):
            return content
        text_parts.append(text)

    return "".join(text_parts)


def _normalize_responses_request_json(body_json: Any) -> tuple[Any, bool]:
    if not isinstance(body_json, dict):
        return body_json, False

    normalized_body = body_json
    changed = False

    model_input = body_json.get("input")
    if isinstance(model_input, list):
        normalized_input: list[Any] = []
        for item in model_input:
            if not isinstance(item, dict):
                normalized_input.append(item)
                continue

            if "role" not in item or "content" not in item:
                normalized_input.append(item)
                continue

            normalized_content = _normalize_message_content(item.get("content"))
            if normalized_content is item.get("content"):
                normalized_input.append(item)
                continue

            changed = True
            normalized_input.append({**item, "content": normalized_content})

        if changed:
            normalized_body = {**normalized_body, "input": normalized_input}

    tools = normalized_body.get("tools")
    if isinstance(tools, list):
        normalized_tools: list[Any] = []
        removed_namespace_tool = False
        for tool in tools:
            if not isinstance(tool, dict):
                normalized_tools.append(tool)
                continue
            tool_type = str(tool.get("type") or "").strip().lower()
            if tool_type == "namespace":
                removed_namespace_tool = True
                continue
            normalized_tools.append(tool)
        if removed_namespace_tool:
            changed = True
            normalized_body = {**normalized_body, "tools": normalized_tools}

    return normalized_body, changed


def _normalize_forwarded_body(request_path: str, body_json: Any, raw_body: bytes) -> tuple[Any, bytes]:
    request = urlsplit(request_path)
    if request.path not in {"/responses", "/v1/responses"}:
        return body_json, raw_body

    normalized_json, changed = _normalize_responses_request_json(body_json)
    if not changed:
        return body_json, raw_body

    return normalized_json, _json_dumps(normalized_json).encode("utf-8")


def _combine_target_url(upstream: SplitResult, request_path: str) -> str:
    request = urlsplit(request_path)
    request_only_path = request.path or "/"
    upstream_prefix = upstream.path.rstrip("/")
    if upstream_prefix:
        combined_path = f"{upstream_prefix}{request_only_path}"
    else:
        combined_path = request_only_path
    if not combined_path.startswith("/"):
        combined_path = f"/{combined_path}"
    return urlunsplit(("", "", combined_path, request.query, ""))


class CaptureProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        upstream: SplitResult,
        log_dir: Path,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.upstream = upstream
        self.log_dir = log_dir
        self.request_log_path = log_dir / "api_requests.jsonl"
        self.response_log_path = log_dir / "api_responses.jsonl"
        self._lock = threading.Lock()
        self._sequence = 0

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        line = _json_dumps(record) + "\n"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)


class CaptureProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy_request()

    @property
    def proxy_server(self) -> CaptureProxyServer:
        return self.server  # type: ignore[return-value]

    def _proxy_request(self) -> None:
        sequence = self.proxy_server.next_sequence()
        body = self._read_body()
        body_json, body_text = _decode_body(body)
        forwarded_body_json, forwarded_body = _normalize_forwarded_body(
            self.path,
            body_json,
            body,
        )
        target = _combine_target_url(self.proxy_server.upstream, self.path)
        forwarded_headers = self._forward_headers(forwarded_body)

        request_record: dict[str, Any] = {
            "schema_version": 1,
            "sequence": sequence,
            "timestamp": _utc_now(),
            "method": self.command,
            "path": self.path,
            "target": target,
            "headers": _redact_headers(dict(self.headers.items())),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_size_bytes": len(body),
        }
        if body_json is not None:
            request_record["body_json"] = body_json
        elif body_text is not None:
            request_record["body_text"] = body_text
        if forwarded_body != body and forwarded_body_json is not None:
            request_record["forwarded_body_json"] = forwarded_body_json
            request_record["forwarded_body_sha256"] = hashlib.sha256(
                forwarded_body
            ).hexdigest()
            request_record["forwarded_body_size_bytes"] = len(forwarded_body)
        self.proxy_server.append_jsonl(self.proxy_server.request_log_path, request_record)

        try:
            response = self._upstream_response(target, forwarded_body, forwarded_headers)
        except Exception as exc:  # pragma: no cover - depends on runtime network
            error = f"proxy upstream error: {exc}"
            self.send_error(502, error)
            self.proxy_server.append_jsonl(
                self.proxy_server.response_log_path,
                {
                    "schema_version": 1,
                    "sequence": sequence,
                    "timestamp": _utc_now(),
                    "status": 502,
                    "reason": "Bad Gateway",
                    "error": error,
                },
            )
            return

        response_headers = dict(response.getheaders())
        self.proxy_server.append_jsonl(
            self.proxy_server.response_log_path,
            {
                "schema_version": 1,
                "sequence": sequence,
                "timestamp": _utc_now(),
                "status": response.status,
                "reason": response.reason,
                "headers": _redact_headers(response_headers),
            },
        )

        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            lowered = key.lower()
            if lowered in HOP_BY_HOP_HEADERS:
                continue
            if lowered == "content-length":
                continue
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()

        if self.command == "HEAD":
            return

        self.close_connection = True
        is_sse = (
            response.getheader("content-type", "").lower().startswith("text/event-stream")
        )
        if is_sse:
            self._stream_sse(response)
        else:
            self._stream_binary(response)

    def _read_body(self) -> bytes:
        content_length = self.headers.get("Content-Length")
        if not content_length:
            return b""
        try:
            length = int(content_length)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _forward_headers(self, body: bytes) -> dict[str, str]:
        forwarded: dict[str, str] = {}
        for key, value in self.headers.items():
            lowered = key.lower()
            if lowered in HOP_BY_HOP_HEADERS:
                continue
            if lowered in {"host", "content-length"}:
                continue
            forwarded[key] = value
        forwarded["Host"] = self.proxy_server.upstream.netloc
        if body:
            # The request body may have been normalized above. Never retain the
            # client's original Content-Length: header names are case-insensitive
            # on the wire but dict keys are not, so retaining e.g. the lowercase
            # spelling and adding this canonical spelling sends two lengths.
            forwarded["Content-Length"] = str(len(body))
        return forwarded

    def _upstream_response(
        self,
        target: str,
        body: bytes,
        headers: dict[str, str],
    ) -> http.client.HTTPResponse:
        upstream = self.proxy_server.upstream
        timeout = 600
        if upstream.scheme == "https":
            connection = http.client.HTTPSConnection(
                upstream.hostname,
                upstream.port or 443,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                upstream.hostname,
                upstream.port or 80,
                timeout=timeout,
            )
        connection.request(
            self.command,
            target,
            body=body if body else None,
            headers=headers,
        )
        return connection.getresponse()

    def _stream_sse(self, response: http.client.HTTPResponse) -> None:
        self._stream_binary(response, chunk_size=4096)

    def _stream_binary(
        self,
        response: http.client.HTTPResponse,
        *,
        chunk_size: int = 64 * 1024,
    ) -> None:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic HTTP reverse proxy with JSONL request capture.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()

    upstream = urlsplit(args.upstream)
    if upstream.scheme not in {"http", "https"} or not upstream.netloc:
        raise SystemExit(f"invalid upstream URL: {args.upstream!r}")

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ready_file = Path(args.ready_file)
    ready_file.parent.mkdir(parents=True, exist_ok=True)

    httpd = CaptureProxyServer(
        (args.host, args.port),
        CaptureProxyHandler,
        upstream=upstream,
        log_dir=log_dir,
    )

    shutdown_event = threading.Event()

    def _shutdown(*_: Any) -> None:
        if shutdown_event.is_set():
            return
        shutdown_event.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    ready_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "host": args.host,
                "port": httpd.server_address[1],
                "upstream": args.upstream,
                "timestamp": _utc_now(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
