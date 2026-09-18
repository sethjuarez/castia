namespace Castia.Spec.Building;

@doc("Offline project-test timeout validation and report classification.")
interface BuildTestingRuntime {
  @sync
  @vector(#{
    name: "project-test-report-pass",
    stage: "callable",
    input: #{ root: "/tmp/agent", exitCode: 0, output: "2 passed", timedOut: false },
    expected: #{ root: "/tmp/agent", status: "pass", exitCode: 0, output: "2 passed", ok: true, scope: "offline" },
  })
  @vector(#{
    name: "project-test-report-fail",
    stage: "callable",
    input: #{ root: "/tmp/agent", exitCode: 1, output: "actual test failure", timedOut: false },
    expected: #{ root: "/tmp/agent", status: "fail", exitCode: 1, output: "actual test failure", ok: false, scope: "offline" },
  })
  @vector(#{
    name: "project-test-report-error",
    stage: "callable",
    input: #{ root: "/tmp/agent", exitCode: 2, output: "collection error", timedOut: false },
    expected: #{ root: "/tmp/agent", status: "error", exitCode: 2, output: "collection error", ok: false, scope: "offline" },
  })
  @vector(#{
    name: "project-test-report-timeout",
    stage: "callable",
    input: #{ root: "/tmp/agent", exitCode: 99, output: "ignored", timedOut: true },
    expected: #{ root: "/tmp/agent", status: "timeout", exitCode: 124, output: "Local tests exceeded the timeout.", ok: false, scope: "offline" },
  })
  projectTestReport(root: string, exitCode: int32, output: string, timedOut: boolean): unknown;

  @sync
  @vector(#{
    name: "test-timeout-accepts-positive-finite",
    stage: "callable",
    input: #{ timeout: 60.0 },
    expected: 60.0,
  })
  @vector(#{
    name: "test-timeout-rejects-zero",
    stage: "callable",
    input: #{ timeout: 0.0 },
    expectedError: #{ message: "timeout must be a finite positive number of seconds" },
  })
  @vector(#{
    name: "test-timeout-rejects-negative",
    stage: "callable",
    input: #{ timeout: -1.0 },
    expectedError: #{ message: "timeout must be a finite positive number of seconds" },
  })
  validateTestTimeout(timeout: float64): float64;
}
