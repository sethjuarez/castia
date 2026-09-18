"""Local pytest execution is functional, bounded, and isolated from the caller."""

import socket
import subprocess
import sys
import textwrap

import pytest

from castia.building import run_project_tests, scaffold_project


def test_runner_executes_actual_scaffold_tests_and_restores_parent(tmp_path):
    scaffold_project(tmp_path)
    original = socket.create_connection
    report = run_project_tests(tmp_path)
    assert report.ok, report.output
    assert report.exit_code == 0
    assert "2 passed" in report.output
    assert report.to_dict()["scope"] == "offline"
    assert not list(tmp_path.glob(".castia-test-output-*"))
    assert socket.create_connection is original


def test_runner_blocks_network_and_child_processes_and_retains_failures(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_network.py").write_text(
        "import socket\n"
        "import subprocess\n"
        "import pytest\n"
        "from castia.building._offline import OfflineOperationError\n"
        "def test_network():\n"
        "    with pytest.raises(OfflineOperationError):\n"
        "        socket.create_connection(('example.com', 443))\n"
        "    with pytest.raises(OfflineOperationError):\n"
        "        subprocess.run(['must-not-start'], check=False)\n"
        "def test_failure():\n"
        "    assert False, 'actual test failure'\n"
    )
    report = run_project_tests(tmp_path)
    assert report.status == "fail", report.output
    assert "1 failed, 1 passed" in report.output
    assert "actual test failure" in report.output


def test_runner_timeout_cleans_created_output(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text(
        "import time\ndef test_wait():\n    time.sleep(60)\n"
    )
    report = run_project_tests(tmp_path, timeout=0.1)
    assert report.status == "timeout"
    assert report.exit_code == 124
    assert not list(tmp_path.glob(".castia-test-output-*"))


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_runner_rejects_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        run_project_tests(tmp_path, timeout=timeout)


def test_runner_requires_real_tests(tmp_path):
    with pytest.raises(ValueError, match="tests directory"):
        run_project_tests(tmp_path)
    (tmp_path / "tests").mkdir()
    with pytest.raises(ValueError, match="no test_"):
        run_project_tests(tmp_path)


def test_cold_platform_metadata_before_guards_and_nested_harness():
    code = textwrap.dedent("""
        import asyncio
        import platform
        import subprocess
        import sys

        from castia import Agent
        from castia.building import AgentTestHarness
        from castia.building._offline import OfflineOperationError, offline_scope

        assert "castia.runtime.dispatch" not in sys.modules
        platform._uname_cache = None
        platform._platform_cache.clear()
        original_popen = subprocess.Popen
        original_uname = platform.uname
        original_platform = platform.platform
        seen = set()

        def first_uname():
            if "uname" not in seen:
                assert subprocess.Popen is original_popen
                seen.add("uname")
            return original_uname()

        def first_platform(*args, **kwargs):
            if "platform" not in seen:
                assert subprocess.Popen is original_popen
                seen.add("platform")
            return original_platform(*args, **kwargs)

        platform.uname = first_uname
        platform.platform = first_platform
        with offline_scope(exclusive=False):
            assert seen == {"uname", "platform"}
            assert platform.node()
            assert platform.platform()
            assert "microsoft.opentelemetry" in sys.modules

            async def check():
                async with AgentTestHarness(Agent()) as test:
                    assert (await test.client.get("/readiness")).status_code == 200

            asyncio.run(check())
            try:
                subprocess.run("ver", shell=True, check=False)
            except OfflineOperationError:
                pass
            else:
                raise AssertionError("User subprocess calls must still be blocked")
        assert subprocess.Popen is original_popen
        print("cold-start passed")
    """)
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold-start passed"
