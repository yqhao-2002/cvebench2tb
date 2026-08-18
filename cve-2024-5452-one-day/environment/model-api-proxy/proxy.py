#!/usr/bin/env python3
"""Fixed-upstream HTTP reverse proxy used as the task's only public egress."""

from __future__ import annotations

import http.client
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit


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
MAX_REQUEST_BYTES = 128 * 1024 * 1024


class FixedUpstreamServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], upstream_url: str) -> None:
        upstream = urlsplit(upstream_url)
        if upstream.scheme not in {"http", "https"} or not upstream.hostname:
            raise ValueError("UPSTREAM_BASE_URL must be an absolute HTTP(S) URL")
        super().__init__(address, FixedUpstreamHandler)
        self.upstream = upstream


class FixedUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_CONNECT(self) -> None:  # noqa: N802
        self.send_error(405, "CONNECT is disabled")

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy()

    @property
    def proxy_server(self) -> FixedUpstreamServer:
        return self.server  # type: ignore[return-value]

    def _proxy(self) -> None:
        if self.path == "/__health":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            body = self._read_body()
            response = self._request_upstream(body)
        except ValueError as exc:
            self.send_error(413, str(exc))
            return
        except Exception as exc:  # pragma: no cover - depends on upstream network
            self.send_error(502, f"model API upstream error: {exc}")
            return

        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() in HOP_BY_HOP_HEADERS or key.lower() == "content-length":
                continue
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        if self.command == "HEAD":
            return
        while chunk := response.read(64 * 1024):
            self.wfile.write(chunk)
            self.wfile.flush()

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        return self.rfile.read(length) if length else b""

    def _request_upstream(self, body: bytes) -> http.client.HTTPResponse:
        upstream = self.proxy_server.upstream
        request = urlsplit(self.path)
        path = request.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        target = urlunsplit(("", "", path, request.query, ""))

        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            lowered = key.lower()
            if lowered in HOP_BY_HOP_HEADERS or lowered in {"host", "content-length"}:
                continue
            headers[key] = value
        headers["Host"] = upstream.netloc
        if body:
            headers["Content-Length"] = str(len(body))

        if upstream.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                upstream.hostname,
                upstream.port or 443,
                timeout=600,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                upstream.hostname,
                upstream.port or 80,
                timeout=600,
            )
        connection.request(self.command, target, body=body, headers=headers)
        return connection.getresponse()


def main() -> None:
    upstream = os.environ.get("UPSTREAM_BASE_URL", "")
    server = FixedUpstreamServer(("0.0.0.0", 8080), upstream)
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
