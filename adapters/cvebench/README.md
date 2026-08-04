# cvebench adapter

Batch-converts CVE-Bench challenges (`/root/cve-bench/src/critical/challenges/`)
into Terminal-Bench 2 (Harbor) dual-container tasks at the repo root, and
health-checks ("体检") the converted tasks end to end.

Design doc: `/root/cve-bench/docs/CVEBENCH-TO-TERMINAL-BENCH-DESIGN.md`.

## Usage

```bash
python adapter.py                             # all challenges x zero_day/one_day
python adapter.py -c CVE-2024-2624            # single challenge
python adapter.py -c CVE-2024-2624 -v one_day # single variant
python adapter.py --image-map OLD=NEW         # extra image rewrite (user images are built-in)
python adapter.py --dry-run                   # render check only

python doctor.py                              # health-check every *-one-day task (9-step smoke)
python doctor.py --include cve-2024-2624-one-day --force
python doctor.py --all-variants --prune-images
```

## doctor.py: what a health check is

Per task: `compose config -q` → clean `down -v` → `up -d` → wait target
healthy → baseline `GET /done` must be `{"status":false}` → `tests/test.sh`
must write reward 0 → `docker exec target bash /tmp/pwn` (fires the RCE
canary) → `GET /done` must turn `{"status":true}` → `tests/test.sh` must
write reward 1 → `down -v`.

Failures are classified by phase (`config/up/healthy/baseline/reward0/flip/
done_true/reward1`) so a broken conversion is pinpointed, not just counted.
Resumable (`PASS` skipped on re-run, `--force` to redo); report at
`doctor-report.json` / `doctor-report.md` after every case.

zero_day/one_day share an identical `environment/` (only instruction.md
differs), so the default sweep checks one variant per challenge; use
`--all-variants` for full coverage.

## How the adapter works

1. `docker compose config --format json` expands the original
   include/extends/variable indirection (`compose-include.yml`,
   `compose-target.yml`, challenge `compose.yml`) into a flat service graph.
2. `agent` service → `main` (Harbor-reserved name): image dropped in favor of
   the template `Dockerfile` build, `agent` DNS alias kept on
   `target_network`.
3. `target` / `secrets_init` / extra sidecars (db, wordpress, ...) are carried
   over verbatim; host bind-mount sources are copied into
   `environment/target-files/` so each task is self-contained.
4. `instruction.md` = `SYSTEM_MESSAGE` (from `src/cvebench/prompts.py`) + the
   variant prompt from the challenge `eval.yml`.
5. `tests/test.sh` polls `http://target:9091/done` and writes
   `/logs/verifier/reward.txt` — identical for every task.
6. Each generated compose is self-checked with `docker compose config -q`.

## Known limitations (per-challenge follow-up)

- Challenges whose sidecars use `build:` instead of published images lose the
  build section (none exist in the critical set — verified by scan).
- `solution/solve.sh` is only generated when the upstream challenge ships
  `solution/solution.sh` (CVE-2024-2624 is the only one).
- `SECRET_SEED` stays at the upstream default (42).
- `cve-2021-44228-target` and `cvebench/ldap-sidecar:2.1.0` are not on
  docker.io (they exist locally); on a fresh machine build them with
  `CVEBENCH_TAG=2.1.0 ./run build CVE-2021-44228` first.

## Self-built image distribution rule

Any image not on docker.io (self-built or rebuilt challenges) must be pushed
to the H-cluster registry as
`registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cve-bench:<name>-target-<tag>`
and registered in `DEFAULT_IMAGE_MAP` in `adapter.py`, then regenerate.
See the repo README for the full procedure and permission notes.

