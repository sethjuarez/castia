namespace Castia.Spec.Observe;

@doc("Pure observe feature-suite validation, execution-plan, and drift comparison contract.")
interface ObserveSuiteRuntime {
  @sync
  @vector(#{
    name: "feature-catalog-has-core-surfaces",
    stage: "callable",
    input: #{},
    expected: #["deploy", "eval", "finetune", "graph", "identity", "model", "optimizer", "project", "runtime", "teams", "telemetry", "tools"]
  })
  featureCatalog(): unknown;

  @sync
  @vector(#{
    name: "validate-suite-normalizes-schema",
    stage: "callable",
    input: #{ suite: #{ name: "daily", features: #["project.read", "model.respond"] } },
    expected: #{ schemaVersion: 1, name: "daily", features: #["project.read", "model.respond"] },
  })
  @vector(#{
    name: "validate-suite-rejects-unknown-feature",
    stage: "callable",
    input: #{ suite: #{ name: "daily", features: #["bogus"] } },
    expectedError: #{ message: "suite contains an unknown feature ID" },
  })
  validateSuite(suite: unknown): unknown;

  @sync
  @vector(#{
    name: "compare-reports-regression-and-lost-coverage",
    stage: "callable",
    input: #{
      baseline: #{ schemaVersion: 1, results: #[#{ featureId: "model.respond", selected: true, status: "pass" }, #{ featureId: "model.stream", selected: true, status: "pass" }] },
      current: #{ schemaVersion: 1, results: #[#{ featureId: "model.respond", selected: true, status: "pass" }] }
    },
    expected: #{ changed: #[#{ featureId: "model.stream", before: "pass", after: null }], regressions: #["model.stream"], lostCoverage: #["model.stream"], newCoverage: #[] },
  })
  compareReports(current: unknown, baseline: unknown): unknown;

  @sync
  @vector(#{
    name: "run-suite-blocks-missing-prerequisite-before-probe",
    stage: "callable",
    input: #{
      suite: #{ name: "daily", features: #["teams.direct", "model.respond"] },
      probes: #{ modelRespond: #{ status: "pass", diagnostic: "ok" }, teamsDirect: #{ status: "pass", diagnostic: "should not run" } },
      prerequisites: #{ `model`: "model", modelUrl: "url" },
      baseline: null,
      generatedAt: "2026-09-15T00:00:00Z"
    },
    expected: #{ teamsDirect: "blocked", modelRespond: "pass", passed: false },
  })
  @vector(#{
    name: "run-suite-comparison-detects-dropped-passing-coverage",
    stage: "callable",
    input: #{
      suite: #{ name: "daily", features: #["project.read"] },
      probes: #{ projectRead: #{ status: "pass", diagnostic: "ok" } },
      prerequisites: #{ projectReadUrl: "url" },
      baseline: #{ schemaVersion: 1, results: #[#{ featureId: "project.read", selected: true, status: "pass" }, #{ featureId: "model.respond", selected: true, status: "pass" }] },
      generatedAt: "2026-09-15T00:00:00Z"
    },
    expected: #{ projectRead: "pass", passed: false, lostCoverage: #["model.respond"], regressions: #["model.respond"] },
  })
  runSuite(suite: unknown, probes: unknown, prerequisites: unknown, baseline: unknown, generatedAt: string): unknown;
}
