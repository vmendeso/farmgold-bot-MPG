r"""Run the full suite; missing/skipped checks are not a complete validation.

No game window or desktop input is needed. Run with the project's interpreter:
    .\.venv\Scripts\python.exe -B -X utf8 scripts/check_tests.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import unittest


def main() -> int:
    os.environ['FARMBOT_DISABLE_REMOTE_OCR'] = '1'  # Unit tests never spend API credits.
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    discovered = suite.countTestCases()
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    complete = (discovered > 0 and result.testsRun == discovered
                and result.wasSuccessful() and not result.skipped and not result.expectedFailures)
    report = {"python": sys.version, "discovered": discovered, "executed": result.testsRun,
              "duration_seconds": round(time.monotonic() - started, 3), "complete": complete,
              "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
              "failures": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
              "errors": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
              "expected_failures": [test.id() for test, _ in result.expectedFailures],
              "unexpected_successes": [test.id() for test in result.unexpectedSuccesses]}
    path = root / "artifacts/test-review/results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nValidation {'complete' if complete else 'INCOMPLETE'}: {path}")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
