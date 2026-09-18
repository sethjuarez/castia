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
import "./integrations/graph.tsp";
import "./integrations/toolbox.tsp";
import "./building/preflight.tsp";
import "./building/scaffold.tsp";
import "./building/testing.tsp";
import "./delivery/azd.tsp";
import "./delivery/manifest.tsp";
import "./evaluation/suite.tsp";
import "./lifecycle/records.tsp";
import "./lifecycle/storage.tsp";
import "./lifecycle/operations.tsp";
import "./lifecycle/acceptance.tsp";
import "./observe/records.tsp";
import "./observe/suite.tsp";
import "./observe/telemetry.tsp";
import "./observe/tracing.tsp";
import "./observe/live.tsp";
import "./runtime/dispatch.tsp";
import "./runtime/router.tsp";

namespace Castia.Spec;

@doc("Aggregate root for Castia's generated cross-runtime contract surface.")
@serializable
model CastiaContracts {
  activity: Protocols.Activity;
  agentConfigResolution: Optimizing.AgentConfigResolution;
}
