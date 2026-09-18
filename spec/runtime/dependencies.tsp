namespace Castia.Spec.Runtime;

@doc("Pure dependency-injection planning. Calling or awaiting dependency functions stays host-specific.")
interface RuntimeDependenciesRuntime {
  @sync
  @vector(#{
    name: "depends-marker-defaults-to-cache",
    stage: "callable",
    input: #{ dependencyName: "model", useCache: true },
    expected: #{ kind: "Depends", dependency: "model", useCache: true },
  })
  @vector(#{
    name: "depends-marker-records-cache-override",
    stage: "callable",
    input: #{ dependencyName: "nonce", useCache: false },
    expected: #{ kind: "Depends", dependency: "nonce", useCache: false },
  })
  dependsMarker(dependencyName: string, useCache: boolean): unknown;

  @sync
  @vector(#{
    name: "dependency-resolve-uses-cache-hit",
    stage: "callable",
    input: #{ cacheKeys: #["model"], dependencyName: "model", useCache: true },
    expected: #{ action: "cache_hit", store: false },
  })
  @vector(#{
    name: "dependency-resolve-calls-and-stores-miss",
    stage: "callable",
    input: #{ cacheKeys: #[], dependencyName: "model", useCache: true },
    expected: #{ action: "call_dependency", store: true },
  })
  @vector(#{
    name: "dependency-resolve-use-cache-false-currently-still-caches",
    stage: "callable",
    input: #{ cacheKeys: #["nonce"], dependencyName: "nonce", useCache: false },
    expected: #{ action: "cache_hit", store: false },
  })
  @doc("Includes useCache so the contract can pin today's Python behavior: _Depends records it, but resolve() currently ignores it and always uses the process cache.")
  resolveDependencyPlan(cacheKeys: string[], dependencyName: string, useCache: boolean): unknown;
}
