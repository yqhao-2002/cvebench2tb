#!/usr/bin/env python3
"""Batch health-check driver ("体检") for converted cvebench Harbor tasks.

For every task directory this runs a 9-step smoke pipeline:

    config   - docker compose config -q
    up       - clean down -v, then up -d (proxy build args passthrough)
    healthy  - wait for the target container to report healthy (bounded)
    baseline - GET target:9091/done from main must be {"status":false}  (no false-pass)
    reward0  - tests/test.sh in main must write reward 0
    flip     - docker exec target bash /tmp/pwn  (fires the RCE canary)
    done_true- GET /done must turn {"status":true}                      (grading chain works)
    reward1  - tests/test.sh must now write reward 1
    down     - down -v (always, even on failure)

zero_day and one_day of the same challenge share an identical environment/
(only instruction.md differs), so by default only *-one-day dirs are checked;
use --all-variants to check everything.

Resumable: cases recorded as PASS in the report are skipped on re-run
(--force to redo). Report is written after every case (crash-safe).

Usage:
    python doctor.py                          # all one-day tasks in the repo
    python doctor.py --include cve-2024-2624-one-day,cnvd-2024-15077-one-day
    python doctor.py --all-variants --prune-images
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = ADAPTER_DIR.parent.parent
REPORT_JSON = ADAPTER_DIR / "doctor-report.json"
REPORT_MD = ADAPTER_DIR / "doctor-report.md"

TASK_NAME_RE = re.compile(r"^(cve|cnvd)-\d{4}-\d+-(one|zero)-day$")
PHASES = ["config", "up", "healthy", "baseline", "reward0", "flip", "done_true", "reward1"]


def sh(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


class CaseFailure(Exception):
    def __init__(self, phase: str, detail: str):
        self.phase, self.detail = phase, detail
        super().__init__(f"{phase}: {detail}")


class CaseRunner:
    def __init__(self, task_dir: Path, healthy_timeout: int, prune_images: bool):
        self.dir = task_dir
        self.name = task_dir.name
        self.env = task_dir / "environment"
        self.proj = self.name.replace("_", "-")
        self.main = f"{self.proj}-main-1"
        self.target = f"{self.proj}-target-1"
        self.healthy_timeout = healthy_timeout
        self.prune_images = prune_images
        self.images: list[str] = []

    def compose(self, *args: str, timeout: int = 120) -> tuple[int, str]:
        return sh(["docker", "compose", "-p", self.proj, *args], cwd=self.env, timeout=timeout)

    def exec_main(self, script: str, timeout: int = 60) -> str:
        rc, out = self.compose("exec", "-T", "main", "sh", "-c", script, timeout=timeout)
        if rc != 0:
            raise CaseFailure("exec", f"main exec failed rc={rc}: {out[:300]}")
        return out

    def done(self) -> str:
        return self.exec_main("curl -s -m 10 http://target:9091/done || true")

    def reward(self) -> str:
        return self.exec_main("cat /logs/verifier/reward.txt 2>/dev/null || echo MISSING").strip()

    def collect_images(self) -> None:
        rc, out = self.compose("config", "--images")
        if rc == 0:
            self.images = out.split()

    def down(self) -> None:
        self.compose("down", "-v", "--remove-orphans", timeout=180)
        if self.prune_images:
            for img in self.images:
                # prune per-case heavy images, but never the shared kali base
                if img.startswith("registry.h.pjlab.org.cn/") or \
                   re.match(r"^cvebench/(cve|cnvd)-.*-(target|server)|^cvebench/(fluent-bit|ldap-sidecar)", img):
                    sh(["docker", "rmi", "-f", img], timeout=300)

    def run(self) -> None:
        started = time.time()
        try:
            rc, out = self.compose("config", "-q")
            if rc != 0:
                raise CaseFailure("config", out[:300])

            self.down()  # clean slate (also collects nothing yet)
            self.collect_images()
            rc, out = self.compose("up", "-d", timeout=900)
            if rc != 0:
                raise CaseFailure("up", out[:400])

            # healthy wait (main only starts after target is healthy; poll both)
            deadline = time.time() + self.healthy_timeout
            while True:
                rc, status = sh(["docker", "inspect", "--format",
                                 "{{.State.Health.Status}}", self.target])
                if rc == 0 and status.strip() == "healthy":
                    break
                if time.time() > deadline:
                    _, ps = self.compose("ps", "-a")
                    raise CaseFailure("healthy", f"target not healthy in {self.healthy_timeout}s; ps:\n{ps[:400]}")
                time.sleep(5)

            out = self.done()
            if '"status":false' not in out.replace(" ", ""):
                raise CaseFailure("baseline", f"expected status:false, got: {out[:200]}")

            self.compose("cp", "../tests/test.sh", "main:/tmp/doctor-test.sh")
            self.exec_main("bash /tmp/doctor-test.sh", timeout=300)
            if self.reward() != "0":
                raise CaseFailure("reward0", f"expected 0, got {self.reward()} (done={self.done()[:200]})")

            rc, out = sh(["docker", "exec", self.target, "bash", "/tmp/pwn"], timeout=120)
            if rc != 0:
                raise CaseFailure("flip", f"pwn.sh rc={rc}: {out[:300]}")

            deadline = time.time() + 60
            out = self.done()
            while '"status":true' not in out.replace(" ", ""):
                if time.time() > deadline:
                    raise CaseFailure("done_true", f"still not true after flip: {out[:200]}")
                time.sleep(3)
                out = self.done()

            self.exec_main("bash /tmp/doctor-test.sh", timeout=300)
            if self.reward() != "1":
                raise CaseFailure("reward1", f"expected 1, got {self.reward()}")
        finally:
            self.down()
            self.elapsed = round(time.time() - started, 1)


def find_tasks(include: list[str] | None, all_variants: bool) -> list[Path]:
    dirs = []
    for d in sorted(REPO_ROOT.iterdir()):
        if not d.is_dir() or not TASK_NAME_RE.match(d.name):
            continue
        if not (d / "environment" / "docker-compose.yaml").exists():
            continue
        if not all_variants and not d.name.endswith("-one-day"):
            continue
        if include and d.name not in include:
            continue
        dirs.append(d)
    return dirs


def write_report(results: dict[str, dict]) -> None:
    REPORT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    lines = ["# doctor report", "",
             f"total: {len(results)}  pass: {sum(1 for r in results.values() if r['status'] == 'PASS')}  "
             f"fail: {sum(1 for r in results.values() if r['status'] == 'FAIL')}", "",
             "| task | status | failed phase | baseline | reward0 | after flip | reward1 | seconds |",
             "|---|---|---|---|---|---|---|---|"]
    for name, r in sorted(results.items()):
        lines.append(f"| {name} | {r['status']} | {r.get('phase', '-')} | {r.get('marks', '-')} | {r.get('elapsed', '-')} |")
    REPORT_MD.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--include", help="comma-separated task dir names (default: all one-day tasks)")
    parser.add_argument("--all-variants", action="store_true", help="also check zero-day dirs")
    parser.add_argument("--force", action="store_true", help="re-check previously PASSed tasks")
    parser.add_argument("--healthy-timeout", type=int, default=900)
    parser.add_argument("--prune-images", action="store_true",
                        help="docker rmi cvebench/registry images after each case (saves disk on full sweeps)")
    args = parser.parse_args()

    tasks = find_tasks(args.include.split(",") if args.include else None, args.all_variants)
    if not tasks:
        print("no matching task dirs", file=sys.stderr)
        return 1

    results: dict[str, dict] = {}
    if REPORT_JSON.exists() and not args.force:
        results = json.loads(REPORT_JSON.read_text())

    todo = [d for d in tasks if args.force or results.get(d.name, {}).get("status") != "PASS"]
    print(f"tasks: {len(tasks)} matched, {len(todo)} to check, {len(tasks) - len(todo)} already PASS")

    for i, task_dir in enumerate(todo, 1):
        runner = CaseRunner(task_dir, args.healthy_timeout, args.prune_images)
        print(f"[{i}/{len(todo)}] {runner.name} ...", flush=True)
        marks = []
        try:
            runner.run()
            results[runner.name] = {"status": "PASS", "elapsed": runner.elapsed}
            print(f"    PASS ({runner.elapsed}s)")
        except CaseFailure as exc:
            results[runner.name] = {"status": "FAIL", "phase": exc.phase,
                                    "detail": exc.detail, "elapsed": runner.elapsed}
            print(f"    FAIL @{exc.phase}: {exc.detail[:200]}")
        except Exception as exc:  # noqa: BLE001 - keep the sweep going
            results[runner.name] = {"status": "FAIL", "phase": "internal", "detail": str(exc)}
            print(f"    FAIL @internal: {exc}")
        write_report(results)

    fails = [n for n, r in results.items() if r["status"] == "FAIL"]
    print(f"\ndone. PASS={sum(1 for r in results.values() if r['status'] == 'PASS')} FAIL={len(fails)}")
    for name in fails:
        print(f"  FAIL {name} @{results[name].get('phase')}: {str(results[name].get('detail'))[:120]}")
    print(f"report: {REPORT_MD}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
