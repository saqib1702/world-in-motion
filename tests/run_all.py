"""Run every offline regression suite and summarise the result.

    python -m tests.run_all

Each suite runs in its own subprocess. That is deliberate: the suites install
stub `pymongo` / `google.genai` modules into `sys.modules` and read module-level
constants at import time, so importing them all into one interpreter would let
one suite's fake state leak into the next and turn a real failure into a pass.

These suites never touch the network or a real database — they are the check to
run before committing. For live Atlas and live Gemini, run
`python scripts/check_connections.py` instead.

Exit code 0 = every suite green.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Ordered cheapest/most-foundational first, so the first failure you see is
#: usually the most informative one.
SUITES = [
    ("tests.test_relation_ids", "canonical agent_id in every relation row"),
    ("tests.test_entity_matching", "which headlines reach which actors"),
    ("tests.test_gemini_fallback", "rate limiting, model fallback, demo mode"),
    ("tests.test_batched_deliberation", "one Gemini call per tick, and its degrade path"),
    ("tests.test_security", "auth, rate limits, input validation, disclosure"),
]

_GREEN = "PASS"
_RED = "FAIL"


def run(module: str) -> tuple[bool, str]:
    """Run one suite. Returns (passed, the suite's own RESULT line)."""
    proc = subprocess.run(
        [sys.executable, "-m", module],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    summary = ""
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            summary = line.strip()

    if proc.returncode != 0:
        # Show the whole thing — a failing suite's output is what you came for.
        print(proc.stdout)
        if proc.stderr.strip():
            print(proc.stderr, file=sys.stderr)

    return proc.returncode == 0, summary or f"(no RESULT line; exit {proc.returncode})"


def main() -> int:
    print("=" * 72)
    print("World in Motion — offline regression suites")
    print("=" * 72)

    failures = []
    for module, blurb in SUITES:
        ok, summary = run(module)
        tag = _GREEN if ok else _RED
        name = module.removeprefix("tests.")
        print(f"  {tag}  {name:<28} {summary}")
        print(f"        {blurb}")
        if not ok:
            failures.append(name)

    print("=" * 72)
    if failures:
        print(f"{len(failures)} suite(s) failed: {', '.join(failures)}")
        return 1

    print(f"All {len(SUITES)} suites passed.")
    print("Offline only — nothing here proves Atlas or Gemini work.")
    print("Run `python scripts/check_connections.py` for the live half.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
