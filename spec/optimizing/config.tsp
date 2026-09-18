namespace Castia.Spec.Optimizing;

@doc("Resolved model instructions and tool definitions for an agent turn.")
model AgentConfigResolution {
  source: string;
  `model`?: string;
  instructions?: string;
  toolDefinitions?: Record<unknown>[];
}

@doc("Portable optimizer config resolver seam; source precedence is pinned by conformance fixtures.")
interface AgentConfigResolver {
  @runtimeCancellable
  @doc("Resolve the active agent config from injected candidate, resolver API, local baseline, or defaults.")
  resolve(configDir?: string): AgentConfigResolution;
}
