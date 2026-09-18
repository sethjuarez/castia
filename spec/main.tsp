import "@typra/emitter";
import "./protocols/activity.tsp";
import "./protocols/responses.tsp";
import "./optimizing/config.tsp";

namespace Castia.Spec;

@doc("Aggregate root for Castia's generated cross-runtime contract surface.")
@serializable
model CastiaContracts {
  activity: Protocols.Activity;
  agentConfigResolution: Optimizing.AgentConfigResolution;
}
