import "@typra/emitter";
import "./protocols/activity.tsp";
import "./protocols/chat.tsp";
import "./protocols/invocations.tsp";
import "./protocols/responses.tsp";
import "./optimizing/config.tsp";
import "./messaging/cards.tsp";
import "./messaging/entities.tsp";
import "./messaging/identity.tsp";
import "./messaging/invokes.tsp";
import "./messaging/routing.tsp";
import "./inference/model.tsp";
import "./inference/tools.tsp";
import "./evaluation/suite.tsp";
import "./lifecycle/records.tsp";

namespace Castia.Spec;

@doc("Aggregate root for Castia's generated cross-runtime contract surface.")
@serializable
model CastiaContracts {
  activity: Protocols.Activity;
  agentConfigResolution: Optimizing.AgentConfigResolution;
}
