"""Private offline pytest worker. Never starts cloud operations."""

from __future__ import annotations

import sys


def main() -> int:
    from ._offline import offline_scope

    # The worker owns its process; each nested harness still acquires the normal
    # exclusive lock. Its exit restores these worker-level guards, not live I/O.
    with offline_scope(exclusive=False):
        try:
            import pytest
        except ImportError:
            print("Project tests require pytest; install castia[test] in this environment.")
            return 2
        return int(pytest.main([
            sys.argv[1], "-q", "-W", "error", "-p", "no:cacheprovider",
            f"--basetemp={sys.argv[2]}",
        ]))


if __name__ == "__main__":
    raise SystemExit(main())
