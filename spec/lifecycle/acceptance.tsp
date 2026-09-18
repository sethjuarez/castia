namespace Castia.Spec.Lifecycle;

@doc("Offline lifecycle acceptance gate and run comparison contract.")
interface LifecycleAcceptanceRuntime {
  @sync
  @vector(#{
    name: "default-gate-python-shape",
    stage: "callable",
    input: #{},
    expected: #{ minimumQuality: 0, maximumQualityDrop: 0, maximumExampleRegressions: 0, maximumLatencySeconds: null, maximumCost: null, requiredMetrics: #["quality"], requireHeldout: true },
  })
  defaultGate(): unknown;

  @sync
  @vector(#{
    name: "compare-runs-accepts-quality-improvement",
    stage: "callable",
    input: #{
      baseline: #{ schemaVersion: 1, kind: "run", agentId: "39404774e110fcea7155f5e091de4ed90bbb55564efbb4d810e5477d22d8607a", datasetId: "0eb100552d27d9ecd3c6a2fa362c119d968b656d826e186b322da5f54a2c70d2", evaluator: #{ name: "rubric", version: "v1", configuration: #{ judge: "judge-v1" } }, split: "heldout", expectedIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"], repeats: 1, results: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{ quality: 0.8, latency_seconds: 0.1, cost: 0.001 }, error: null }, #{ exampleId: "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3", repetition: 0, metrics: #{ quality: 0.8, latency_seconds: 0.1, cost: 0.001 }, error: null }] },
      candidate: #{ schemaVersion: 1, kind: "run", agentId: "82fd115ba1551d85eeee23a53cd346cd80e7e11b6bef4cff3e7484394d187068", datasetId: "0eb100552d27d9ecd3c6a2fa362c119d968b656d826e186b322da5f54a2c70d2", evaluator: #{ name: "rubric", version: "v1", configuration: #{ judge: "judge-v1" } }, split: "heldout", expectedIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"], repeats: 1, results: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{ quality: 0.9, latency_seconds: 0.2, cost: 0.002 }, error: null }, #{ exampleId: "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3", repetition: 0, metrics: #{ quality: 0.9, latency_seconds: 0.2, cost: 0.002 }, error: null }] },
      gate: #{}
    },
    expected: #{ schemaVersion: 1, kind: "decision", baselineRunId: "c7855ee3695618145922205fe2bf72aec1cc381567a38d902bf7631dbb215eb3", candidateRunId: "4bdaea86a8d8e7a58437654020d429594e59477c8bd6a4718e579679503cc7e7", baselineAgentId: "39404774e110fcea7155f5e091de4ed90bbb55564efbb4d810e5477d22d8607a", candidateAgentId: "82fd115ba1551d85eeee23a53cd346cd80e7e11b6bef4cff3e7484394d187068", accepted: true, reasons: #[], aggregates: #{ baseline: #{ quality: 0.8, latency_seconds: 0.1, cost: 0.001 }, candidate: #{ quality: 0.9, latency_seconds: 0.2, cost: 0.002 }, delta: #{ quality: 0.09999999999999998, latency_seconds: 0.1, cost: 0.001 } }, regressions: #[], gate: #{ minimumQuality: 0, maximumQualityDrop: 0, maximumExampleRegressions: 0, maximumLatencySeconds: null, maximumCost: null, requiredMetrics: #["quality"], requireHeldout: true } },
  })
  @vector(#{
    name: "compare-runs-rejects-cost-limit",
    stage: "callable",
    input: #{
      baseline: #{ schemaVersion: 1, kind: "run", agentId: "39404774e110fcea7155f5e091de4ed90bbb55564efbb4d810e5477d22d8607a", datasetId: "0eb100552d27d9ecd3c6a2fa362c119d968b656d826e186b322da5f54a2c70d2", evaluator: #{ name: "rubric", version: "v1", configuration: #{ judge: "judge-v1" } }, split: "heldout", expectedIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"], repeats: 1, results: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{ quality: 0.8, latency_seconds: 0.1, cost: 0.001 }, error: null }, #{ exampleId: "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3", repetition: 0, metrics: #{ quality: 0.8, latency_seconds: 0.1, cost: 0.001 }, error: null }] },
      candidate: #{ schemaVersion: 1, kind: "run", agentId: "82fd115ba1551d85eeee23a53cd346cd80e7e11b6bef4cff3e7484394d187068", datasetId: "0eb100552d27d9ecd3c6a2fa362c119d968b656d826e186b322da5f54a2c70d2", evaluator: #{ name: "rubric", version: "v1", configuration: #{ judge: "judge-v1" } }, split: "heldout", expectedIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"], repeats: 1, results: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{ quality: 0.9, latency_seconds: 0.2, cost: 0.002 }, error: null }, #{ exampleId: "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3", repetition: 0, metrics: #{ quality: 0.9, latency_seconds: 0.2, cost: 0.002 }, error: null }] },
      gate: #{ maximumCost: 0.0001 }
    },
    expected: #{ schemaVersion: 1, kind: "decision", baselineRunId: "c7855ee3695618145922205fe2bf72aec1cc381567a38d902bf7631dbb215eb3", candidateRunId: "4bdaea86a8d8e7a58437654020d429594e59477c8bd6a4718e579679503cc7e7", baselineAgentId: "39404774e110fcea7155f5e091de4ed90bbb55564efbb4d810e5477d22d8607a", candidateAgentId: "82fd115ba1551d85eeee23a53cd346cd80e7e11b6bef4cff3e7484394d187068", accepted: false, reasons: #["candidate cost exceeds limit"], aggregates: #{ baseline: #{ quality: 0.8, latency_seconds: 0.1, cost: 0.001 }, candidate: #{ quality: 0.9, latency_seconds: 0.2, cost: 0.002 }, delta: #{ quality: 0.09999999999999998, latency_seconds: 0.1, cost: 0.001 } }, regressions: #[], gate: #{ minimumQuality: 0, maximumQualityDrop: 0, maximumExampleRegressions: 0, maximumLatencySeconds: null, maximumCost: 0.0001, requiredMetrics: #["quality"], requireHeldout: true } },
  })
  @vector(#{
    name: "compare-runs-uses-precise-mean-summation",
    stage: "callable",
    input: #{
      baseline: #{ schemaVersion: 1, kind: "run", agentId: "1111111111111111111111111111111111111111111111111111111111111111", datasetId: "2222222222222222222222222222222222222222222222222222222222222222", evaluator: #{ name: "rubric", version: "v1", configuration: #{} }, split: "heldout", expectedIds: #["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"], repeats: 1, results: #[#{ exampleId: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", repetition: 0, metrics: #{ quality: 0.10000000000000002 }, error: null }, #{ exampleId: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", repetition: 0, metrics: #{ quality: 0.10000000000000002 }, error: null }, #{ exampleId: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", repetition: 0, metrics: #{ quality: 0.10000000000000002 }, error: null }] },
      candidate: #{ schemaVersion: 1, kind: "run", agentId: "3333333333333333333333333333333333333333333333333333333333333333", datasetId: "2222222222222222222222222222222222222222222222222222222222222222", evaluator: #{ name: "rubric", version: "v1", configuration: #{} }, split: "heldout", expectedIds: #["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"], repeats: 1, results: #[#{ exampleId: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", repetition: 0, metrics: #{ quality: 0.0254458609934608 }, error: null }, #{ exampleId: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", repetition: 0, metrics: #{ quality: 0.5414124727934966 }, error: null }, #{ exampleId: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", repetition: 0, metrics: #{ quality: 0.9391491627785106 }, error: null }] },
      gate: #{ maximumExampleRegressions: 1 }
    },
    expected: #{ schemaVersion: 1, kind: "decision", baselineRunId: "9929d141eedec5fe873db1c6252e37cb470040e5ab9e68de65c0e3ab05079bf2", candidateRunId: "0d6ce980f158bfaa7b8e180edd2afdb146dad1e558205b4cfec351cfc72cbbb7", baselineAgentId: "1111111111111111111111111111111111111111111111111111111111111111", candidateAgentId: "3333333333333333333333333333333333333333333333333333333333333333", accepted: true, reasons: #[], aggregates: #{ baseline: #{ quality: 0.10000000000000002 }, candidate: #{ quality: 0.502002498855156 }, delta: #{ quality: 0.402002498855156 } }, regressions: #["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], gate: #{ minimumQuality: 0, maximumQualityDrop: 0, maximumExampleRegressions: 1, maximumLatencySeconds: null, maximumCost: null, requiredMetrics: #["quality"], requireHeldout: true } },
  })
  compareRuns(baseline: unknown, candidate: unknown, gate: unknown): unknown;
}
