use castia::building::{CastiaBuildPreflightRuntime, CastiaBuildTestingRuntime};
use castia::delivery::{
    plan_manifest as build_manifest_plan, CastiaDeliveryAzdRuntime, CastiaDeliveryManifestRuntime,
};
use castia::evaluation::CastiaEvaluationSuiteRuntime;
use castia::finetuning::{
    build_dpo_method as build_finetune_dpo_method, build_rft_job as build_finetune_rft_job,
    build_rft_method as build_finetune_rft_method, build_sft_job as build_finetune_sft_job,
    build_sft_method as build_finetune_sft_method,
    string_check_grader as build_finetune_string_check_grader,
    validate_dpo_example as build_validate_dpo_example, validate_grader as build_validate_grader,
    validate_rft_dataset as build_validate_rft_dataset,
    validate_rft_example as build_validate_rft_example,
    validate_sft_example as build_validate_sft_example, CastiaFinetuningTrainingRuntime,
};
use castia::hosting::{
    readiness_body as build_readiness_body, sse_event as build_sse_event,
    token_response_access_token as build_token_response_access_token,
    user_fic_token_request as build_user_fic_token_request, wire_endpoints as build_wire_endpoints,
    CastiaHostingCredentialsRuntime, CastiaHostingServerRuntime,
};
use castia::inference::{try_reasoning_param, CastiaModelRuntime, CastiaToolCatalogRuntime};
use castia::integrations::{
    knowledge_base_mcp_tool as build_knowledge_base_mcp_tool,
    toolbox_mcp_tool as build_toolbox_mcp_tool, CastiaIntegrationsGraphRuntime,
    CastiaIntegrationsToolboxRuntime,
};
use castia::lifecycle::{
    canonical_json, check_public, compare_runs, content_hash, curate_dataset, dataset_jsonl,
    default_gate, diff_candidates, evaluate_outcomes, example_id, get_artifact, journal_read,
    normalize_record, put_artifact, record_id, safe_path, stage_candidate,
};
use castia::messaging::{
    try_require_agentic_user, CastiaCardsRuntime, CastiaEntitiesRuntime, CastiaIdentityRuntime,
    CastiaInvokesRuntime, CastiaRoutingRuntime,
};
use castia::model::{
    Activity, ActivityRuntime, AgentConfigResolver, BuildPreflightRuntime, BuildTestingRuntime,
    CardsRuntime, ChatRuntime, DeliveryAzdRuntime, DeliveryManifestRuntime, EntitiesRuntime,
    EvaluationSuiteRuntime, FinetuningTrainingRuntime, HostingCredentialsRuntime,
    HostingServerRuntime, IdentityRuntime, IntegrationsGraphRuntime, IntegrationsToolboxRuntime,
    InvocationsRuntime, InvokesRuntime, LifecycleAcceptanceRuntime, LifecycleOperationsRuntime,
    LifecycleRecordsRuntime, LifecycleStorageRuntime, LoadContext, ModelRuntime,
    ObserveLiveRuntime, ObserveRecordsRuntime, ObserveSuiteRuntime, ObserveTelemetryRuntime,
    ObserveTracingRuntime, ResponsesRuntime, RoutingRuntime, RuntimeContextRuntime,
    RuntimeDispatchRuntime, RuntimeRouterRuntime, SaveContext, ToolCatalogRuntime,
};
use castia::observe::{
    CastiaObserveLiveRuntime, CastiaObserveRecordsRuntime, CastiaObserveSuiteRuntime,
    CastiaObserveTelemetryRuntime, CastiaObserveTracingRuntime,
};
use castia::optimizing::CastiaAgentConfigResolver;
use castia::protocols::{
    CastiaActivityRuntime, CastiaChatRuntime, CastiaInvocationsRuntime, CastiaResponsesRuntime,
};
use castia::runtime::{
    decorate_message as build_decorate_message, include_plan as build_include_plan,
    responses_only_projection as build_responses_only_projection, turn_cite as build_turn_cite,
    wire_dispatch_plan as build_wire_dispatch_plan, CastiaRuntimeContextRuntime,
    CastiaRuntimeDispatchRuntime, CastiaRuntimeRouterRuntime,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::fs;
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Mutex, OnceLock};

static OPTIMIZER_ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
const OPTIMIZER_ENV: &[&str] = &[
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "OPTIMIZATION_CONFIG",
    "OPTIMIZATION_CANDIDATE_ID",
    "OPTIMIZATION_RESOLVE_ENDPOINT",
    "OPTIMIZATION_LOCAL_DIR",
];

pub struct Context {
    pub contract: String,
    pub operation: String,
    pub vector: Value,
    pub provider: Option<String>,
    pub target_api: Option<String>,
    pub doubles: Value,
    pub base_dir: String,
}

pub type SyncInvoke = fn(&Value, &Context) -> Result<Value, VectorError>;
pub type AsyncInvoke = Box<
    dyn Fn(&Value, &Context) -> Pin<Box<dyn Future<Output = Result<Value, VectorError>> + Send>>
        + Send
        + Sync,
>;
pub type Normalize = fn(&Value, &Context) -> Value;

pub enum Invoke {
    Sync(SyncInvoke),
    Async(AsyncInvoke),
}

pub struct Adapter {
    pub invoke: Invoke,
    pub normalize: Option<Normalize>,
}

pub struct VectorError {
    pub message: String,
    pub payload: Option<Value>,
}

impl VectorError {
    fn new(message: String) -> Self {
        Self {
            message,
            payload: None,
        }
    }
}

pub fn adapters() -> HashMap<&'static str, Adapter> {
    HashMap::from([
        (
            "ActivityRuntime.agenticInstanceId",
            sync(agentic_instance_id),
        ),
        ("ActivityRuntime.agenticTenantId", sync(agentic_tenant_id)),
        ("ActivityRuntime.agenticUser", sync(agentic_user)),
        ("ActivityRuntime.channel", sync(channel)),
        ("ActivityRuntime.isAgenticRequest", sync(is_agentic_request)),
        ("ActivityRuntime.mentions", sync(mentions)),
        (
            "AgentConfigResolver.resolve",
            sync_with_normalize(agent_config_resolve, normalize_agent_config_source),
        ),
        (
            "BuildScaffoldRuntime.scaffoldFiles",
            sync_with_normalize(build_scaffold_files, build_scaffold_to_camel),
        ),
        (
            "BuildPreflightRuntime.preflightReport",
            sync_with_normalize(build_preflight_report, build_scaffold_to_camel),
        ),
        (
            "BuildPreflightRuntime.requiredEnvDiagnostics",
            sync(build_required_env_diagnostics),
        ),
        (
            "BuildPreflightRuntime.deploymentContextDiagnostics",
            sync(build_deployment_context_diagnostics),
        ),
        (
            "BuildTestingRuntime.projectTestReport",
            sync_with_normalize(build_project_test_report, build_scaffold_to_camel),
        ),
        (
            "BuildTestingRuntime.validateTestTimeout",
            sync(build_validate_test_timeout),
        ),
        (
            "CardsRuntime.actionChips",
            sync_with_normalize(cards_action_chips, normalize_special_json_keys),
        ),
        (
            "CardsRuntime.adaptiveCard",
            sync_with_normalize(cards_adaptive_card, normalize_special_json_keys),
        ),
        (
            "CardsRuntime.decisionCard",
            sync_with_normalize(cards_decision_card, normalize_special_json_keys),
        ),
        (
            "CardsRuntime.suggestedActions",
            sync(cards_suggested_actions),
        ),
        (
            "DeliveryAzdRuntime.validateDeploymentInput",
            sync_with_normalize(delivery_validate_deployment_input, delivery_azd_to_camel),
        ),
        (
            "DeliveryAzdRuntime.validateEnvironmentValues",
            sync(delivery_validate_environment_values),
        ),
        (
            "DeliveryAzdRuntime.verifyPayload",
            sync_with_normalize(delivery_verify_payload, delivery_azd_to_camel),
        ),
        (
            "DeliveryAzdRuntime.commandErrorMessage",
            sync(delivery_command_error_message),
        ),
        (
            "DeliveryManifestRuntime.publishableProtocols",
            sync(delivery_manifest_publishable_protocols),
        ),
        (
            "DeliveryManifestRuntime.skippedProtocols",
            sync(delivery_manifest_skipped_protocols),
        ),
        (
            "DeliveryManifestRuntime.serviceProtocolItems",
            sync(delivery_manifest_service_protocol_items),
        ),
        (
            "DeliveryManifestRuntime.planManifest",
            sync(delivery_manifest_plan),
        ),
        (
            "ChatRuntime.body",
            sync_with_normalize(chat_body, normalize_generated_id),
        ),
        ("ChatRuntime.lastUserText", sync(chat_last_user_text)),
        (
            "EntitiesRuntime.citation",
            sync_with_normalize(entities_citation, normalize_special_json_keys),
        ),
        (
            "EntitiesRuntime.feedbackChannelData",
            sync(entities_feedback_channel_data),
        ),
        (
            "EntitiesRuntime.mentionEntity",
            sync(entities_mention_entity),
        ),
        (
            "EntitiesRuntime.messageEntity",
            sync_with_normalize(entities_message_entity, normalize_special_json_keys),
        ),
        (
            "EntitiesRuntime.sensitivityLabel",
            sync_with_normalize(entities_sensitivity_label, normalize_special_json_keys),
        ),
        (
            "EvaluationSuiteRuntime.buildGenerateArgv",
            sync(evaluation_build_generate_argv),
        ),
        (
            "EvaluationSuiteRuntime.buildRunArgv",
            sync(evaluation_build_run_argv),
        ),
        (
            "EvaluationSuiteRuntime.buildUpdateArgv",
            sync(evaluation_build_update_argv),
        ),
        (
            "EvaluationSuiteRuntime.loadSuite",
            sync(evaluation_load_suite),
        ),
        (
            "EvaluationSuiteRuntime.readRubric",
            sync(evaluation_read_rubric),
        ),
        (
            "EvaluationSuiteRuntime.validateSuite",
            sync(evaluation_validate_suite),
        ),
        (
            "FinetuningTrainingRuntime.buildDpoMethod",
            sync(finetune_build_dpo_method),
        ),
        (
            "FinetuningTrainingRuntime.buildRftJob",
            sync(finetune_build_rft_job),
        ),
        (
            "FinetuningTrainingRuntime.buildRftMethod",
            sync(finetune_build_rft_method),
        ),
        (
            "FinetuningTrainingRuntime.buildSftJob",
            sync(finetune_build_sft_job),
        ),
        (
            "FinetuningTrainingRuntime.buildSftMethod",
            sync(finetune_build_sft_method),
        ),
        (
            "FinetuningTrainingRuntime.stringCheckGrader",
            sync(finetune_string_check_grader),
        ),
        (
            "FinetuningTrainingRuntime.validateDpoExample",
            sync(finetune_validate_dpo_example),
        ),
        (
            "FinetuningTrainingRuntime.validateGrader",
            sync(finetune_validate_grader),
        ),
        (
            "FinetuningTrainingRuntime.validateRftDataset",
            sync(finetune_validate_rft_dataset),
        ),
        (
            "FinetuningTrainingRuntime.validateRftExample",
            sync(finetune_validate_rft_example),
        ),
        (
            "FinetuningTrainingRuntime.validateSftExample",
            sync(finetune_validate_sft_example),
        ),
        ("HostingCredentialsRuntime.bearer", sync(hosting_bearer)),
        (
            "HostingCredentialsRuntime.isLocalRun",
            sync(hosting_is_local_run),
        ),
        (
            "HostingCredentialsRuntime.agenticIdentityFromEnv",
            sync(hosting_agentic_identity_from_env),
        ),
        (
            "HostingCredentialsRuntime.tenantTokenEndpoint",
            sync(hosting_tenant_token_endpoint),
        ),
        (
            "HostingCredentialsRuntime.instanceTokenRequest",
            sync(hosting_instance_token_request),
        ),
        (
            "HostingCredentialsRuntime.userFicTokenRequest",
            sync(hosting_user_fic_token_request),
        ),
        (
            "HostingCredentialsRuntime.botConnectorCredential",
            sync(hosting_bot_connector_credential),
        ),
        (
            "HostingCredentialsRuntime.hostingScopes",
            sync(hosting_scopes),
        ),
        (
            "HostingCredentialsRuntime.tokenResponseAccessToken",
            sync(hosting_token_response_access_token),
        ),
        (
            "HostingServerRuntime.readinessBody",
            sync(hosting_server_readiness_body),
        ),
        (
            "HostingServerRuntime.sseEvent",
            sync(hosting_server_sse_event),
        ),
        (
            "HostingServerRuntime.wireEndpoints",
            sync(hosting_server_wire_endpoints),
        ),
        (
            "IdentityRuntime.agenticUserId",
            sync(identity_agentic_user_id),
        ),
        (
            "IdentityRuntime.requireAgenticUser",
            sync(identity_require_agentic_user),
        ),
        (
            "IntegrationsToolboxRuntime.platformEndpointEnv",
            sync(integrations_platform_endpoint_env),
        ),
        (
            "IntegrationsToolboxRuntime.composeToolboxEndpoint",
            sync(integrations_compose_toolbox_endpoint),
        ),
        (
            "IntegrationsToolboxRuntime.resolveToolboxEndpoint",
            sync(integrations_resolve_toolbox_endpoint),
        ),
        (
            "IntegrationsToolboxRuntime.toolboxMcpTool",
            sync_with_normalize(integrations_toolbox_mcp_tool, integrations_toolbox_to_camel),
        ),
        (
            "IntegrationsToolboxRuntime.knowledgeBaseMcpTool",
            sync_with_normalize(
                integrations_knowledge_base_mcp_tool,
                integrations_toolbox_to_camel,
            ),
        ),
        (
            "IntegrationsGraphRuntime.mailboxMessagesUrl",
            sync(graph_mailbox_messages_url),
        ),
        (
            "IntegrationsGraphRuntime.summarizeMailboxMessage",
            sync(graph_summarize_mailbox_message),
        ),
        (
            "IntegrationsGraphRuntime.sendMailPayload",
            sync(graph_send_mail_payload),
        ),
        (
            "IntegrationsGraphRuntime.sendMailResult",
            sync(graph_send_mail_result),
        ),
        (
            "IntegrationsGraphRuntime.replyMailRequest",
            sync(graph_reply_mail_request),
        ),
        (
            "IntegrationsGraphRuntime.replyMailResult",
            sync(graph_reply_mail_result),
        ),
        (
            "IntegrationsGraphRuntime.driveUploadUrl",
            sync(graph_drive_upload_url),
        ),
        (
            "IntegrationsGraphRuntime.driveShareInvitePayload",
            sync(graph_drive_share_invite_payload),
        ),
        (
            "IntegrationsGraphRuntime.driveUploadResult",
            sync(graph_drive_upload_result),
        ),
        (
            "IntegrationsGraphRuntime.driveShareResult",
            sync(graph_drive_share_result),
        ),
        (
            "LifecycleRecordsRuntime.canonicalJson",
            sync(lifecycle_canonical_json),
        ),
        (
            "LifecycleRecordsRuntime.checkPublic",
            sync(lifecycle_check_public),
        ),
        (
            "LifecycleRecordsRuntime.contentHash",
            sync(lifecycle_content_hash),
        ),
        (
            "LifecycleRecordsRuntime.exampleId",
            sync(lifecycle_example_id),
        ),
        (
            "LifecycleRecordsRuntime.normalizeRecord",
            sync_with_normalize(lifecycle_normalize_record, lifecycle_record_to_camel),
        ),
        (
            "LifecycleRecordsRuntime.recordId",
            sync(lifecycle_record_id),
        ),
        (
            "LifecycleRecordsRuntime.safePath",
            sync(lifecycle_safe_path),
        ),
        (
            "LifecycleStorageRuntime.getArtifact",
            sync(lifecycle_get_artifact),
        ),
        (
            "LifecycleStorageRuntime.journalRead",
            sync_with_normalize(lifecycle_journal_read, lifecycle_record_to_camel),
        ),
        (
            "LifecycleStorageRuntime.putArtifact",
            sync(lifecycle_put_artifact),
        ),
        (
            "LifecycleAcceptanceRuntime.compareRuns",
            sync_with_normalize(lifecycle_compare_runs, lifecycle_record_to_camel),
        ),
        (
            "LifecycleAcceptanceRuntime.defaultGate",
            sync_with_normalize(lifecycle_default_gate, lifecycle_record_to_camel),
        ),
        (
            "LifecycleOperationsRuntime.curateDataset",
            sync_with_normalize(lifecycle_curate_dataset, lifecycle_record_to_camel),
        ),
        (
            "LifecycleOperationsRuntime.datasetJsonl",
            sync(lifecycle_dataset_jsonl),
        ),
        (
            "LifecycleOperationsRuntime.evaluateOutcomes",
            asynchronous(lifecycle_evaluate_outcomes),
        ),
        (
            "LifecycleOperationsRuntime.stageCandidate",
            sync_with_normalize(lifecycle_stage_candidate, lifecycle_record_to_camel),
        ),
        (
            "LifecycleOperationsRuntime.diffCandidates",
            sync(lifecycle_diff_candidates),
        ),
        (
            "RuntimeRouterRuntime.registeredProtocols",
            sync(runtime_registered_protocols),
        ),
        (
            "RuntimeRouterRuntime.includePlan",
            sync(runtime_include_plan),
        ),
        (
            "RuntimeRouterRuntime.responsesOnlyProjection",
            sync(runtime_responses_only_projection),
        ),
        (
            "RuntimeContextRuntime.turnCite",
            sync(runtime_context_turn_cite),
        ),
        (
            "RuntimeContextRuntime.decorateMessage",
            sync(runtime_context_decorate_message),
        ),
        (
            "RuntimeDispatchRuntime.activityDispatchPlan",
            sync(runtime_activity_dispatch_plan),
        ),
        (
            "RuntimeDispatchRuntime.invokeDispatchPlan",
            sync(runtime_invoke_dispatch_plan),
        ),
        (
            "RuntimeDispatchRuntime.wireDispatchPlan",
            sync(runtime_wire_dispatch_plan),
        ),
        (
            "RuntimeDispatchRuntime.activityResultText",
            sync(runtime_activity_result_text),
        ),
        (
            "RuntimeDispatchRuntime.invokeResultBody",
            sync(runtime_invoke_result_body),
        ),
        (
            "RuntimeDispatchRuntime.returnBody",
            sync(runtime_return_body),
        ),
        (
            "RuntimeDispatchRuntime.streamChunks",
            sync(runtime_stream_chunks),
        ),
        (
            "ModelRuntime.instructionsParam",
            sync(model_instructions_param),
        ),
        (
            "ObserveRecordsRuntime.normalizeRecord",
            sync_with_normalize(observe_normalize_record, observe_record_to_camel),
        ),
        (
            "ObserveRecordsRuntime.summarize",
            sync_with_normalize(observe_summarize, observe_record_to_camel),
        ),
        (
            "ObserveSuiteRuntime.featureCatalog",
            sync(observe_feature_catalog),
        ),
        (
            "ObserveSuiteRuntime.validateSuite",
            sync_with_normalize(observe_validate_suite, observe_suite_to_camel),
        ),
        (
            "ObserveSuiteRuntime.compareReports",
            sync_with_normalize(observe_compare_reports, observe_suite_to_camel),
        ),
        ("ObserveSuiteRuntime.runSuite", sync(observe_run_suite)),
        (
            "ObserveTracingRuntime.invokeAgentSpan",
            sync_with_normalize(observe_invoke_agent_span, observe_tracing_to_camel),
        ),
        (
            "ObserveTracingRuntime.executeToolSpan",
            sync_with_normalize(observe_execute_tool_span, observe_tracing_to_camel),
        ),
        (
            "ObserveTelemetryRuntime.traceQueryKql",
            sync(observe_trace_query_kql),
        ),
        (
            "ObserveTelemetryRuntime.httpError",
            sync_with_normalize(observe_http_error, observe_telemetry_to_camel),
        ),
        (
            "ObserveTelemetryRuntime.verifyProbe",
            sync_with_normalize(observe_verify_probe, observe_telemetry_to_camel),
        ),
        (
            "ObserveLiveRuntime.validateLimits",
            sync_with_normalize(observe_validate_limits, observe_live_to_camel),
        ),
        (
            "ObserveLiveRuntime.validateLiveConfig",
            sync_with_normalize(observe_validate_live_config, observe_live_to_camel),
        ),
        (
            "ObserveLiveRuntime.responseTextResult",
            sync_with_normalize(observe_response_text_result, observe_live_to_camel),
        ),
        (
            "ObserveLiveRuntime.streamResult",
            sync_with_normalize(observe_stream_result, observe_live_to_camel),
        ),
        (
            "ObserveLiveRuntime.optimizerStatus",
            sync(observe_optimizer_status),
        ),
        (
            "ObserveLiveRuntime.candidateConfig",
            sync(observe_candidate_config),
        ),
        ("ModelRuntime.publicToolSpec", sync(model_public_tool_spec)),
        ("ModelRuntime.reasoningParam", sync(model_reasoning_param)),
        ("InvocationsRuntime.body", sync(invocations_body)),
        ("InvocationsRuntime.inputText", sync(invocations_input_text)),
        ("InvokesRuntime.cardAction", sync(invokes_card_action)),
        (
            "InvokesRuntime.cardInvokeResponse",
            sync(invokes_card_invoke_response),
        ),
        (
            "InvokesRuntime.feedbackPayload",
            sync(invokes_feedback_payload),
        ),
        (
            "InvokesRuntime.messageInvokeResponse",
            sync(invokes_message_invoke_response),
        ),
        ("ResponsesRuntime.inputText", sync(responses_input_text)),
        (
            "ResponsesRuntime.outputBody",
            sync_with_normalize(responses_output_body, normalize_generated_id),
        ),
        (
            "RoutingRuntime.teamsDirectMessage",
            sync(routing_teams_direct_message),
        ),
        (
            "RoutingRuntime.teamsGroupChatMessage",
            sync(routing_teams_group_chat_message),
        ),
        (
            "RoutingRuntime.teamsTaggedChannelMessage",
            sync(routing_teams_tagged_channel_message),
        ),
        (
            "ToolCatalogRuntime.activityToolNames",
            sync(tool_catalog_activity_tool_names),
        ),
        (
            "ToolCatalogRuntime.agentToolNames",
            sync(tool_catalog_agent_tool_names),
        ),
        (
            "ToolCatalogRuntime.graphToolNames",
            sync(tool_catalog_graph_tool_names),
        ),
        ("ToolCatalogRuntime.toolSpec", sync(tool_catalog_tool_spec)),
    ])
}

pub fn waivers() -> HashMap<&'static str, &'static str> {
    HashMap::new()
}

pub fn doubles() -> Value {
    Value::Null
}

fn sync(invoke: SyncInvoke) -> Adapter {
    Adapter {
        invoke: Invoke::Sync(invoke),
        normalize: None,
    }
}

fn sync_with_normalize(invoke: SyncInvoke, normalize: Normalize) -> Adapter {
    Adapter {
        invoke: Invoke::Sync(invoke),
        normalize: Some(normalize),
    }
}

fn asynchronous<F, Fut>(invoke: F) -> Adapter
where
    F: Fn(Value, Context) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = Result<Value, VectorError>> + Send + 'static,
{
    Adapter {
        invoke: Invoke::Async(Box::new(move |input, ctx| {
            let input = input.clone();
            let ctx = Context {
                contract: ctx.contract.clone(),
                operation: ctx.operation.clone(),
                vector: ctx.vector.clone(),
                provider: ctx.provider.clone(),
                target_api: ctx.target_api.clone(),
                doubles: ctx.doubles.clone(),
                base_dir: ctx.base_dir.clone(),
            };
            Box::pin(invoke(input, ctx))
        })),
        normalize: None,
    }
}

fn activity(input: &Value) -> Result<Activity, VectorError> {
    let Some(value) = input.get("activity") else {
        return Err(VectorError {
            message: "missing activity input".to_string(),
            payload: None,
        });
    };
    Activity::try_load_from_value(value, &LoadContext::default()).map_err(|error| VectorError {
        message: error.to_string(),
        payload: None,
    })
}

fn agentic_instance_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.agentic_instance_id(&activity)
    ))
}

fn agentic_tenant_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.agentic_tenant_id(&activity)
    ))
}

fn agentic_user(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.agentic_user(&activity)
    ))
}

fn channel(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(CastiaActivityRuntime.channel(&activity)))
}

fn is_agentic_request(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.is_agentic_request(&activity)
    ))
}

fn mentions(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    let mentions = CastiaActivityRuntime.mentions(&activity);
    Ok(Value::Array(
        mentions
            .iter()
            .map(|mention| mention.to_value(&SaveContext::default()))
            .collect(),
    ))
}

fn cards_adaptive_card(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaCardsRuntime.adaptive_card(
        input.get("body").unwrap_or(&Value::Null),
        input.get("card").unwrap_or(&Value::Null),
    ))
}

fn cards_suggested_actions(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaCardsRuntime.suggested_actions(input.get("actions").unwrap_or(&Value::Null)))
}

fn cards_action_chips(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaCardsRuntime.action_chips(
        input.get("actions").unwrap_or(&Value::Null),
        &input
            .get("prompt")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    ))
}

fn cards_decision_card(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(castia::messaging::decision_card_with_options(
        input.get("actions").unwrap_or(&Value::Null),
        input
            .get("prompt")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input.get("card").unwrap_or(&Value::Null),
    ))
}

fn entities_citation(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEntitiesRuntime.citation(
        &input
            .get("position")
            .and_then(Value::as_i64)
            .unwrap_or_default()
            .try_into()
            .unwrap_or_default(),
        &input
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("abstractText")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        input.get("keywords").unwrap_or(&Value::Null),
        &input
            .get("icon")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    ))
}

fn entities_sensitivity_label(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEntitiesRuntime.sensitivity_label(
        &input
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    ))
}

fn entities_message_entity(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEntitiesRuntime.message_entity(
        &input
            .get("aiGenerated")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
        input.get("citations").unwrap_or(&Value::Null),
        input.get("sensitivity").unwrap_or(&Value::Null),
    ))
}

fn entities_feedback_channel_data(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEntitiesRuntime.feedback_channel_data(
        &input
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("default")
            .to_string(),
    ))
}

fn entities_mention_entity(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEntitiesRuntime.mention_entity(
        &input
            .get("accountId")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    ))
}

fn evaluation_build_generate_argv(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEvaluationSuiteRuntime
        .build_generate_argv(input.get("options").unwrap_or(&Value::Null)))
}

fn evaluation_build_update_argv(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(
        CastiaEvaluationSuiteRuntime
            .build_update_argv(input.get("options").unwrap_or(&Value::Null)),
    )
}

fn evaluation_build_run_argv(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEvaluationSuiteRuntime.build_run_argv(input.get("options").unwrap_or(&Value::Null)))
}

fn evaluation_read_rubric(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaEvaluationSuiteRuntime.read_rubric(input.get("value").unwrap_or(&Value::Null)))
}

fn evaluation_load_suite(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let temp = temp_dir(&vector_temp_label(ctx));
    let result = (|| {
        write_vector_files(&temp, &ctx.vector)?;
        let path = vector_path(input, "path", &temp)?;
        let mut value = CastiaEvaluationSuiteRuntime.load_suite(&path);
        replace_temp_path(&mut value, &temp.to_string_lossy());
        Ok(value)
    })();
    let _ = fs::remove_dir_all(temp);
    result
}

fn evaluation_validate_suite(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let temp = temp_dir(&vector_temp_label(ctx));
    let result = (|| {
        write_vector_files(&temp, &ctx.vector)?;
        let path = vector_path(input, "path", &temp)?;
        Ok(CastiaEvaluationSuiteRuntime.validate_suite(&path))
    })();
    let _ = fs::remove_dir_all(temp);
    result
}

fn finetune_build_dpo_method(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_finetune_dpo_method(input.get("hyperparameters"))
        .map_err(|error| VectorError::new(error.to_string()))
}

fn finetune_build_rft_job(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let model = input
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let training_file = input
        .get("trainingFile")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let validation_file = input
        .get("validationFile")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let grader = input.get("grader").unwrap_or(&Value::Null);
    let suffix = input.get("suffix").and_then(Value::as_str);
    let seed = input.get("seed").and_then(Value::as_i64);
    let response_format = input.get("responseFormat").filter(|value| !value.is_null());
    build_finetune_rft_job(
        model,
        training_file,
        validation_file,
        grader,
        input.get("hyperparameters"),
        response_format,
        suffix,
        seed,
    )
    .map_err(|error| VectorError::new(error.to_string()))
}

fn finetune_build_rft_method(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let grader = input.get("grader").unwrap_or(&Value::Null);
    let response_format = input.get("responseFormat").filter(|value| !value.is_null());
    build_finetune_rft_method(grader, input.get("hyperparameters"), response_format)
        .map_err(|error| VectorError::new(error.to_string()))
}

fn finetune_build_sft_job(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let model = input
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let training_file = input
        .get("trainingFile")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let validation_file = input.get("validationFile").and_then(Value::as_str);
    let suffix = input.get("suffix").and_then(Value::as_str);
    let seed = input.get("seed").and_then(Value::as_i64);
    build_finetune_sft_job(
        model,
        training_file,
        validation_file,
        input.get("hyperparameters"),
        suffix,
        seed,
    )
    .map_err(|error| VectorError::new(error.to_string()))
}

fn finetune_build_sft_method(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_finetune_sft_method(input.get("hyperparameters"))
        .map_err(|error| VectorError::new(error.to_string()))
}

fn finetune_string_check_grader(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let name = input
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let grader_input = input
        .get("input")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let reference = input
        .get("reference")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let operation = input
        .get("operation")
        .and_then(Value::as_str)
        .unwrap_or("eq");
    build_finetune_string_check_grader(name, grader_input, reference, operation)
        .map_err(|error| VectorError::new(error.to_string()))
}

fn finetune_validate_dpo_example(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(build_validate_dpo_example(
        input.get("row").unwrap_or(&Value::Null)
    )))
}

fn finetune_validate_grader(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(build_validate_grader(
        input.get("grader").unwrap_or(&Value::Null)
    )))
}

fn finetune_validate_rft_dataset(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let rows = input
        .get("rows")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let grader = input.get("grader");
    let split = input
        .get("split")
        .and_then(Value::as_str)
        .unwrap_or("dataset");
    Ok(json!(build_validate_rft_dataset(rows, grader, split)))
}

fn finetune_validate_rft_example(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(build_validate_rft_example(
        input.get("row").unwrap_or(&Value::Null)
    )))
}

fn finetune_validate_sft_example(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(build_validate_sft_example(
        input.get("row").unwrap_or(&Value::Null)
    )))
}

fn hosting_bearer(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(
        CastiaHostingCredentialsRuntime.bearer(&string_input(input, "token")?),
    ))
}

fn hosting_is_local_run(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::Bool(
        CastiaHostingCredentialsRuntime.is_local_run(input.get("env").unwrap_or(&Value::Null)),
    ))
}

fn hosting_agentic_identity_from_env(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaHostingCredentialsRuntime
        .agentic_identity_from_env(input.get("env").unwrap_or(&Value::Null)))
}

fn hosting_tenant_token_endpoint(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(
        CastiaHostingCredentialsRuntime.tenant_token_endpoint(&string_input(input, "tenantId")?),
    ))
}

fn hosting_instance_token_request(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaHostingCredentialsRuntime.instance_token_request(
        &string_input(input, "instanceClientId")?,
        &string_input(input, "agentAssertion")?,
    ))
}

fn hosting_user_fic_token_request(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_user_fic_token_request(
        &string_input(input, "instanceClientId")?,
        &string_input(input, "agentAssertion")?,
        &string_input(input, "instanceToken")?,
        &string_input(input, "agenticUserId")?,
        &string_input(input, "scope")?,
    )
    .map_err(vector_error)
}

fn hosting_bot_connector_credential(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaHostingCredentialsRuntime
        .bot_connector_credential(input.get("env").unwrap_or(&Value::Null)))
}

fn hosting_scopes(_: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaHostingCredentialsRuntime.hosting_scopes())
}

fn hosting_token_response_access_token(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_token_response_access_token(
        input
            .get("status")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32,
        input.get("body").unwrap_or(&Value::Null),
        &string_input(input, "text")?,
    )
    .map(Value::String)
    .map_err(vector_error)
}

fn hosting_server_readiness_body(_: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(build_readiness_body()))
}

fn hosting_server_sse_event(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(build_sse_event(
        &string_input(input, "eventType")?,
        input.get("payload").unwrap_or(&Value::Null),
    )))
}

fn hosting_server_wire_endpoints(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(build_wire_endpoints(&string_array_input(
        input,
        "wireProtocols",
    )?))
}

fn identity_agentic_user_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaIdentityRuntime.agentic_user_id(&activity)
    ))
}

fn identity_require_agentic_user(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    match try_require_agentic_user(&activity) {
        Ok(value) => Ok(Value::String(value)),
        Err(error) => Err(VectorError {
            message: error.to_string(),
            payload: Some(Value::String(error.to_string())),
        }),
    }
}

fn integrations_platform_endpoint_env(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let name = required(input, "name")?
        .as_str()
        .ok_or_else(|| VectorError::new("platformEndpointEnv.name must be a string".to_string()))?;
    Ok(Value::String(
        CastiaIntegrationsToolboxRuntime.platform_endpoint_env(&name.to_string()),
    ))
}

fn integrations_compose_toolbox_endpoint(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let project_endpoint = required(input, "projectEndpoint")?
        .as_str()
        .ok_or_else(|| {
            VectorError::new("composeToolboxEndpoint.projectEndpoint must be a string".to_string())
        })?;
    let name = required(input, "name")?.as_str().ok_or_else(|| {
        VectorError::new("composeToolboxEndpoint.name must be a string".to_string())
    })?;
    let version = input
        .get("version")
        .and_then(Value::as_str)
        .map(str::to_string);
    Ok(Value::String(
        CastiaIntegrationsToolboxRuntime.compose_toolbox_endpoint(
            &project_endpoint.to_string(),
            &name.to_string(),
            &version,
        ),
    ))
}

fn integrations_resolve_toolbox_endpoint(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let env = required(input, "env")?;
    Ok(CastiaIntegrationsToolboxRuntime
        .resolve_toolbox_endpoint(env)
        .map(Value::String)
        .unwrap_or(Value::Null))
}

fn integrations_toolbox_mcp_tool(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let config = required(input, "config")?;
    build_toolbox_mcp_tool(config).map_err(vector_error)
}

fn integrations_knowledge_base_mcp_tool(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let config = required(input, "config")?;
    build_knowledge_base_mcp_tool(config).map_err(vector_error)
}

fn graph_mailbox_messages_url(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(
        CastiaIntegrationsGraphRuntime.mailbox_messages_url(
            &(input.get("top").and_then(Value::as_i64).unwrap_or_default() as i32),
            &input
                .get("unreadOnly")
                .and_then(Value::as_bool)
                .unwrap_or_default(),
        ),
    ))
}

fn graph_summarize_mailbox_message(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime
        .summarize_mailbox_message(input.get("message").unwrap_or(&Value::Null)))
}

fn graph_send_mail_payload(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime.send_mail_payload(
        &string_input(input, "to")?,
        &string_input(input, "subject")?,
        &string_input(input, "body")?,
    ))
}

fn graph_send_mail_result(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime.send_mail_result(
        &(input
            .get("status")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
        &string_input(input, "grantedScopes")?,
        &string_input(input, "detail")?,
    ))
}

fn graph_reply_mail_request(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime.reply_mail_request(
        &string_input(input, "messageId")?,
        &string_input(input, "body")?,
    ))
}

fn graph_reply_mail_result(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime.reply_mail_result(
        &(input
            .get("status")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
        &string_input(input, "grantedScopes")?,
        &string_input(input, "detail")?,
    ))
}

fn graph_drive_upload_url(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(
        CastiaIntegrationsGraphRuntime.drive_upload_url(&string_input(input, "path")?),
    ))
}

fn graph_drive_share_invite_payload(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime
        .drive_share_invite_payload(&string_input(input, "requesterObjectId")?))
}

fn graph_drive_upload_result(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime.drive_upload_result(
        &(input
            .get("status")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
        &string_input(input, "grantedScopes")?,
        &string_input(input, "webUrl")?,
        input.get("shared").unwrap_or(&Value::Null),
        &string_input(input, "detail")?,
    ))
}

fn graph_drive_share_result(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaIntegrationsGraphRuntime.drive_share_result(
        &string_input(input, "itemId")?,
        &string_input(input, "requesterObjectId")?,
        &(input
            .get("status")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
        &string_input(input, "detail")?,
    ))
}

fn lifecycle_canonical_json(input: &Value, _: &Context) -> Result<Value, VectorError> {
    canonical_json(input.get("value").unwrap_or(&Value::Null))
        .map(Value::String)
        .map_err(vector_error)
}

fn lifecycle_content_hash(input: &Value, _: &Context) -> Result<Value, VectorError> {
    content_hash(input.get("value").unwrap_or(&Value::Null))
        .map(Value::String)
        .map_err(vector_error)
}

fn lifecycle_safe_path(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let path = input
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default();
    safe_path(path).map(Value::String).map_err(vector_error)
}

fn lifecycle_check_public(input: &Value, _: &Context) -> Result<Value, VectorError> {
    check_public(input.get("value").unwrap_or(&Value::Null))
        .map(|()| Value::Bool(true))
        .map_err(vector_error)
}

fn lifecycle_normalize_record(input: &Value, _: &Context) -> Result<Value, VectorError> {
    normalize_record(&lifecycle_record_to_snake(
        input.get("value").unwrap_or(&Value::Null),
    ))
    .map_err(vector_error)
}

fn lifecycle_record_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    record_id(&lifecycle_record_to_snake(
        input.get("value").unwrap_or(&Value::Null),
    ))
    .map(Value::String)
    .map_err(vector_error)
}

fn lifecycle_example_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    example_id(&lifecycle_record_to_snake(
        input.get("value").unwrap_or(&Value::Null),
    ))
    .map(Value::String)
    .map_err(vector_error)
}

fn lifecycle_put_artifact(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let temp = temp_dir(&vector_temp_label(ctx));
    let result = (|| {
        write_vector_files(&temp, &ctx.vector)?;
        let root = vector_path(input, "root", &temp)?;
        put_artifact(
            root,
            &lifecycle_record_to_snake(input.get("record").unwrap_or(&Value::Null)),
        )
        .map(Value::String)
        .map_err(vector_error)
    })();
    let _ = fs::remove_dir_all(temp);
    result
}

fn lifecycle_get_artifact(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let temp = temp_dir(&vector_temp_label(ctx));
    let result = (|| {
        write_vector_files(&temp, &ctx.vector)?;
        let root = vector_path(input, "root", &temp)?;
        let Some(id) = input.get("id").and_then(Value::as_str) else {
            return Err(VectorError {
                message: "missing id input".to_string(),
                payload: None,
            });
        };
        get_artifact(root, id)
            .map(|record| lifecycle_keys(&record, false))
            .map_err(vector_error)
    })();
    let _ = fs::remove_dir_all(temp);
    result
}

fn lifecycle_journal_read(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let temp = temp_dir(&vector_temp_label(ctx));
    let result = (|| {
        write_vector_files(&temp, &ctx.vector)?;
        let root = vector_path(input, "root", &temp)?;
        journal_read(root).map_err(vector_error)
    })();
    let _ = fs::remove_dir_all(temp);
    result
}

fn lifecycle_default_gate(_: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(default_gate())
}

fn lifecycle_compare_runs(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let baseline = lifecycle_record_to_snake(input.get("baseline").unwrap_or(&Value::Null));
    let candidate = lifecycle_record_to_snake(input.get("candidate").unwrap_or(&Value::Null));
    let gate = lifecycle_record_to_snake(input.get("gate").unwrap_or(&Value::Null));
    compare_runs(&baseline, &candidate, &gate).map_err(vector_error)
}

fn lifecycle_curate_dataset(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let traces = input
        .get("traces")
        .map(lifecycle_record_to_snake)
        .and_then(|value| value.as_array().cloned())
        .ok_or_else(|| VectorError {
            message: "missing traces input".to_string(),
            payload: None,
        })?;
    let redaction_version = input
        .get("redactionVersion")
        .and_then(Value::as_str)
        .ok_or_else(|| VectorError {
            message: "missing redactionVersion input".to_string(),
            payload: None,
        })?;
    let heldout_fraction = input
        .get("heldoutFraction")
        .and_then(Value::as_f64)
        .ok_or_else(|| VectorError {
            message: "missing heldoutFraction input".to_string(),
            payload: None,
        })?;
    let seed = input
        .get("seed")
        .and_then(Value::as_str)
        .ok_or_else(|| VectorError {
            message: "missing seed input".to_string(),
            payload: None,
        })?;
    curate_dataset(&traces, redaction_version, heldout_fraction, seed).map_err(vector_error)
}

fn lifecycle_dataset_jsonl(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let dataset = lifecycle_record_to_snake(input.get("dataset").unwrap_or(&Value::Null));
    let split = input
        .get("split")
        .and_then(Value::as_str)
        .ok_or_else(|| VectorError {
            message: "missing split input".to_string(),
            payload: None,
        })?;
    dataset_jsonl(&dataset, split)
        .map(Value::String)
        .map_err(vector_error)
}

fn lifecycle_stage_candidate(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let temp = temp_dir(&vector_temp_label(ctx));
    let result = (|| {
        write_vector_files(&temp, &ctx.vector)?;
        let root = vector_path(input, "root", &temp)?;
        let files = input
            .get("files")
            .and_then(Value::as_array)
            .ok_or_else(|| VectorError {
                message: "files must be an array".to_string(),
                payload: None,
            })?
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .map(str::to_string)
                    .ok_or_else(|| VectorError {
                        message: "file path must be nonempty text".to_string(),
                        payload: None,
                    })
            })
            .collect::<Result<Vec<_>, _>>()?;
        let baseline = lifecycle_record_to_snake(input.get("baseline").unwrap_or(&Value::Null));
        let agent = lifecycle_record_to_snake(input.get("agent").unwrap_or(&Value::Null));
        stage_candidate(root, &files, &baseline, &agent).map_err(vector_error)
    })();
    let _ = fs::remove_dir_all(temp);
    result
}

fn lifecycle_diff_candidates(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let baseline = lifecycle_record_to_snake(input.get("baseline").unwrap_or(&Value::Null));
    let candidate = lifecycle_record_to_snake(input.get("candidate").unwrap_or(&Value::Null));
    diff_candidates(&baseline, &candidate).map_err(vector_error)
}

fn runtime_registered_protocols(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(CastiaRuntimeRouterRuntime.registered_protocols(
        &(input
            .get("activityRouteCount")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
        &string_array_input(input, "invokeNames")?,
        &string_array_input(input, "wireProtocols")?,
    )))
}

fn runtime_include_plan(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_include_plan(
        &string_array_input(input, "existingWire")?,
        &string_array_input(input, "incomingWire")?,
        &string_array_input(input, "existingInvokes")?,
        &string_array_input(input, "incomingInvokes")?,
    )
    .map_err(vector_error)
}

fn runtime_responses_only_projection(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_responses_only_projection(
        &string_input(input, "name")?,
        input
            .get("hasResponses")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
        &string_array_input(input, "toolNames")?,
    )
    .map_err(vector_error)
}

fn runtime_context_turn_cite(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(build_turn_cite(
        input.get("citations").unwrap_or(&Value::Null),
        &string_input(input, "name")?,
        &string_input(input, "url")?,
        &string_input(input, "abstractText")?,
        input.get("keywords").unwrap_or(&Value::Null),
        &string_input(input, "icon")?,
    ))
}

fn runtime_context_decorate_message(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(build_decorate_message(
        input.get("payload").unwrap_or(&Value::Null),
        input.get("turn").unwrap_or(&Value::Null),
        input
            .get("aiGenerated")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
        input.get("citations").unwrap_or(&Value::Null),
        input.get("sensitivity").unwrap_or(&Value::Null),
        input.get("feedback").and_then(Value::as_str),
        input.get("importance").and_then(Value::as_str),
    ))
}

fn runtime_activity_dispatch_plan(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaRuntimeDispatchRuntime.activity_dispatch_plan(
        input.get("parameters").unwrap_or(&Value::Null),
        &string_input(input, "text")?,
    ))
}

fn runtime_invoke_dispatch_plan(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaRuntimeDispatchRuntime.invoke_dispatch_plan(
        input.get("parameters").unwrap_or(&Value::Null),
        input.get("value").unwrap_or(&Value::Null),
    ))
}

fn runtime_wire_dispatch_plan(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_wire_dispatch_plan(
        &string_input(input, "handlerName")?,
        input.get("parameters").unwrap_or(&Value::Null),
        &string_input(input, "text")?,
        input
            .get("streaming")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
    )
    .map_err(vector_error)
}

fn runtime_activity_result_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaRuntimeDispatchRuntime
        .activity_result_text(input.get("result").unwrap_or(&Value::Null))
        .map(Value::String)
        .unwrap_or(Value::Null))
}

fn runtime_invoke_result_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(
        CastiaRuntimeDispatchRuntime
            .invoke_result_body(input.get("result").unwrap_or(&Value::Null)),
    )
}

fn runtime_return_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(Value::String(
        CastiaRuntimeDispatchRuntime.return_body(input.get("result").unwrap_or(&Value::Null)),
    ))
}

fn runtime_stream_chunks(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(CastiaRuntimeDispatchRuntime.stream_chunks(
        input.get("result").unwrap_or(&Value::Null)
    )))
}

async fn lifecycle_evaluate_outcomes(input: Value, _: Context) -> Result<Value, VectorError> {
    let agent = lifecycle_record_to_snake(input.get("agent").unwrap_or(&Value::Null));
    let dataset = lifecycle_record_to_snake(input.get("dataset").unwrap_or(&Value::Null));
    let evaluator = lifecycle_record_to_snake(input.get("evaluator").unwrap_or(&Value::Null));
    let outcomes = lifecycle_record_to_snake(input.get("outcomes").unwrap_or(&Value::Null));
    let split = input
        .get("split")
        .and_then(Value::as_str)
        .ok_or_else(|| VectorError {
            message: "missing split input".to_string(),
            payload: None,
        })?;
    let repeats = input
        .get("repeats")
        .and_then(Value::as_i64)
        .ok_or_else(|| VectorError {
            message: "missing repeats input".to_string(),
            payload: None,
        })?;
    let concurrency = input
        .get("concurrency")
        .and_then(Value::as_i64)
        .ok_or_else(|| VectorError {
            message: "missing concurrency input".to_string(),
            payload: None,
        })?;
    let timeout_seconds = input
        .get("timeoutSeconds")
        .and_then(Value::as_f64)
        .ok_or_else(|| VectorError {
            message: "missing timeoutSeconds input".to_string(),
            payload: None,
        })?;
    evaluate_outcomes(
        &agent,
        &dataset,
        &evaluator,
        &outcomes,
        split,
        repeats,
        concurrency,
        timeout_seconds,
    )
    .await
    .map(|record| lifecycle_keys(&record, false))
    .map_err(vector_error)
}

fn vector_error(error: impl std::fmt::Display) -> VectorError {
    VectorError {
        message: error.to_string(),
        payload: None,
    }
}

fn panic_message(panic: Box<dyn std::any::Any + Send>) -> String {
    if let Some(message) = panic.downcast_ref::<String>() {
        message.clone()
    } else if let Some(message) = panic.downcast_ref::<&str>() {
        (*message).to_string()
    } else {
        "adapter panicked".to_string()
    }
}

fn observe_normalize_record(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaObserveRecordsRuntime.normalize_record(
        input.get("row").unwrap_or(&Value::Null),
        &input
            .get("includeContent")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
    ))
}

fn observe_summarize(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let records = observe_record_to_snake(input.get("records").unwrap_or(&Value::Null));
    Ok(CastiaObserveRecordsRuntime.summarize(&records))
}

fn observe_feature_catalog(_: &Value, _: &Context) -> Result<Value, VectorError> {
    let catalog = CastiaObserveSuiteRuntime.feature_catalog();
    let empty = Vec::new();
    let surfaces = catalog["features"]
        .as_array()
        .unwrap_or(&empty)
        .iter()
        .filter_map(|feature| feature["id"].as_str())
        .map(|id| id.split('.').next().unwrap_or_default())
        .collect::<std::collections::HashSet<_>>();
    let mut surfaces = surfaces.into_iter().collect::<Vec<_>>();
    surfaces.sort_unstable();
    Ok(Value::Array(
        surfaces
            .into_iter()
            .map(|surface| Value::String(surface.to_string()))
            .collect(),
    ))
}

fn observe_validate_suite(input: &Value, _: &Context) -> Result<Value, VectorError> {
    castia::observe::validate_suite(&observe_suite_to_snake(
        input.get("suite").unwrap_or(&Value::Null),
    ))
    .map_err(vector_error)
}

fn observe_compare_reports(input: &Value, _: &Context) -> Result<Value, VectorError> {
    castia::observe::compare_reports(
        &observe_suite_to_snake(input.get("current").unwrap_or(&Value::Null)),
        &observe_suite_to_snake(input.get("baseline").unwrap_or(&Value::Null)),
    )
    .map_err(vector_error)
}

fn observe_run_suite(input: &Value, context: &Context) -> Result<Value, VectorError> {
    let baseline = observe_suite_to_snake(input.get("baseline").unwrap_or(&Value::Null));
    let report = CastiaObserveSuiteRuntime.run_suite(
        &observe_suite_to_snake(input.get("suite").unwrap_or(&Value::Null)),
        &observe_suite_to_snake(input.get("probes").unwrap_or(&Value::Null)),
        &observe_suite_to_snake(input.get("prerequisites").unwrap_or(&Value::Null)),
        &baseline,
        &input
            .get("generatedAt")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    );
    let expected = context
        .vector
        .get("expected")
        .cloned()
        .unwrap_or(Value::Null);
    if expected.is_null() {
        return Ok(observe_suite_to_camel(&report, context));
    }
    let mut actual = serde_json::Map::new();
    if let Some(expected_object) = expected.as_object() {
        for key in expected_object.keys() {
            match key.as_str() {
                "passed" => {
                    actual.insert(key.clone(), report["passed"].clone());
                }
                "lostCoverage" => {
                    actual.insert(key.clone(), report["comparison"]["lost_coverage"].clone());
                }
                "regressions" => {
                    actual.insert(key.clone(), report["comparison"]["regressions"].clone());
                }
                feature => {
                    let feature_id = suite_feature_key(feature);
                    let empty = Vec::new();
                    let status = report["results"]
                        .as_array()
                        .unwrap_or(&empty)
                        .iter()
                        .find(|row| row["feature_id"].as_str() == Some(feature_id.as_str()))
                        .map(|row| row["status"].clone())
                        .unwrap_or(Value::Null);
                    actual.insert(key.clone(), status);
                }
            }
        }
    }
    Ok(Value::Object(actual))
}

fn observe_invoke_agent_span(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaObserveTracingRuntime.invoke_agent_span(
        &input
            .get("name")
            .and_then(Value::as_str)
            .map(str::to_string),
        &input
            .get("envName")
            .and_then(Value::as_str)
            .map(str::to_string),
        &input
            .get("envVersion")
            .and_then(Value::as_str)
            .map(str::to_string),
        &input
            .get("system")
            .and_then(Value::as_str)
            .map(str::to_string),
    ))
}

fn observe_execute_tool_span(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaObserveTracingRuntime.execute_tool_span(
        &input
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("system")
            .and_then(Value::as_str)
            .map(str::to_string),
    ))
}

fn observe_trace_query_kql(input: &Value, context: &Context) -> Result<Value, VectorError> {
    let query = observe_telemetry_to_snake(input.get("query").unwrap_or(&Value::Null));
    let kql = castia::observe::trace_query_kql(&query).map_err(vector_error)?;
    for needle in context
        .vector
        .get("expectedContains")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        if !kql.contains(needle) {
            return Err(VectorError {
                message: format!("query missing expected fragment: {needle}"),
                payload: Some(Value::String(kql)),
            });
        }
    }
    for needle in context
        .vector
        .get("expectedNotContains")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        if kql.contains(needle) {
            return Err(VectorError {
                message: format!("query contained forbidden fragment: {needle}"),
                payload: Some(Value::String(kql)),
            });
        }
    }
    if let Some(suffix) = context.vector.get("expectedSuffix").and_then(Value::as_str) {
        if !kql.ends_with(suffix) {
            return Err(VectorError {
                message: format!("query did not end with: {suffix}"),
                payload: Some(Value::String(kql)),
            });
        }
    }
    Ok(Value::Null)
}

fn observe_http_error(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaObserveTelemetryRuntime.http_error(
        &(input
            .get("status")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
    ))
}

fn observe_verify_probe(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let attempts = observe_telemetry_to_snake(input.get("attempts").unwrap_or(&Value::Null));
    Ok(CastiaObserveTelemetryRuntime.verify_probe(
        &attempts,
        &input
            .get("probeTag")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("timeoutSeconds")
            .and_then(Value::as_f64)
            .unwrap_or_default(),
        &input
            .get("pollSeconds")
            .and_then(Value::as_f64)
            .unwrap_or_default(),
        &(input
            .get("maxAttempts")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
    ))
}

fn observe_validate_limits(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let limits = observe_live_to_snake(input.get("limits").unwrap_or(&Value::Null));
    castia::observe::validate_limits(&limits).map_err(vector_error)
}

fn observe_validate_live_config(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let config = observe_live_to_snake(input.get("config").unwrap_or(&Value::Null));
    castia::observe::validate_live_config(&config).map_err(vector_error)
}

fn observe_response_text_result(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let response = observe_live_to_snake(input.get("response").unwrap_or(&Value::Null));
    Ok(CastiaObserveLiveRuntime.response_text_result(
        &response,
        &input
            .get("expected")
            .and_then(Value::as_str)
            .map(str::to_string),
    ))
}

fn observe_stream_result(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let events = observe_live_to_snake(input.get("events").unwrap_or(&Value::Null));
    Ok(CastiaObserveLiveRuntime.stream_result(&events))
}

fn observe_optimizer_status(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let payload = observe_live_to_snake(input.get("payload").unwrap_or(&Value::Null));
    castia::observe::optimizer_status(&payload, input.get("expectedId").and_then(Value::as_str))
        .map(Value::String)
        .map_err(vector_error)
}

fn observe_candidate_config(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let payload = observe_live_to_snake(input.get("payload").unwrap_or(&Value::Null));
    castia::observe::candidate_config(&payload).map_err(vector_error)
}

fn model_instructions_param(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaModelRuntime.instructions_param(
        &input
            .get("instructions")
            .and_then(Value::as_str)
            .map(str::to_string),
    ))
}

fn model_reasoning_param(input: &Value, _: &Context) -> Result<Value, VectorError> {
    match try_reasoning_param(input.get("effort").and_then(Value::as_str)) {
        Ok(value) => Ok(value),
        Err(error) => Err(VectorError {
            message: error.to_string(),
            payload: Some(Value::String(error.to_string())),
        }),
    }
}

fn model_public_tool_spec(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let spec = restore_private_tool_keys(input.get("spec").unwrap_or(&Value::Null));
    Ok(CastiaModelRuntime
        .public_tool_spec(&spec, input.get("toolDefinitions").unwrap_or(&Value::Null)))
}

fn tool_catalog_activity_tool_names(_: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaToolCatalogRuntime.activity_tool_names())
}

fn tool_catalog_graph_tool_names(_: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaToolCatalogRuntime.graph_tool_names())
}

fn tool_catalog_agent_tool_names(_: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaToolCatalogRuntime.agent_tool_names())
}

fn tool_catalog_tool_spec(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaToolCatalogRuntime.tool_spec(input.get("tool").unwrap_or(&Value::Null)))
}

fn invokes_message_invoke_response(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaInvokesRuntime.message_invoke_response(
        &input
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    ))
}

fn invokes_card_invoke_response(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaInvokesRuntime.card_invoke_response(input.get("card").unwrap_or(&Value::Null)))
}

fn invokes_feedback_payload(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(CastiaInvokesRuntime.feedback_payload(&activity))
}

fn invokes_card_action(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(CastiaInvokesRuntime.card_action(&activity))
}

fn routing_teams_direct_message(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaRoutingRuntime.teams_direct_message(&activity)
    ))
}

fn routing_teams_group_chat_message(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaRoutingRuntime.teams_group_chat_message(&activity)
    ))
}

fn routing_teams_tagged_channel_message(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaRoutingRuntime.teams_tagged_channel_message(&activity)
    ))
}

fn agent_config_resolve(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let _guard = OPTIMIZER_ENV_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let saved_env = save_optimizer_env();
    let temp = temp_dir(&ctx.operation);
    let result = (|| {
        apply_vector_env(&ctx.vector);
        write_vector_files(&temp, &ctx.vector)?;
        let config_dir = input.get("configDir").and_then(Value::as_str).map(|value| {
            if value == "$temp" {
                temp.to_string_lossy().to_string()
            } else {
                value.to_string()
            }
        });
        let resolution = CastiaAgentConfigResolver.resolve_best_effort(config_dir.as_deref());
        Ok(resolution.to_value(&SaveContext::default()))
    })();
    restore_optimizer_env(saved_env);
    let _ = fs::remove_dir_all(temp);
    result
}

fn build_scaffold_files(input: &Value, _: &Context) -> Result<Value, VectorError> {
    castia::building::scaffold_files(
        input
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("model")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    )
    .map_err(vector_error)
}

fn build_preflight_report(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let protocols = input
        .get("protocols")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect::<Vec<_>>();
    Ok(CastiaBuildPreflightRuntime.preflight_report(
        &input
            .get("root")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("appTarget")
            .and_then(Value::as_str)
            .unwrap_or("main:app")
            .to_string(),
        &protocols,
        input.get("diagnostics").unwrap_or(&Value::Null),
    ))
}

fn build_required_env_diagnostics(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let required_env = input
        .get("requiredEnv")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect::<Vec<_>>();
    Ok(CastiaBuildPreflightRuntime.required_env_diagnostics(
        input.get("environment").unwrap_or(&Value::Null),
        &required_env,
    ))
}

fn build_deployment_context_diagnostics(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaBuildPreflightRuntime.deployment_context_diagnostics(
        input.get("environment").unwrap_or(&Value::Null),
        &input
            .get("deployment")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
    ))
}

fn build_project_test_report(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(CastiaBuildTestingRuntime.project_test_report(
        &input
            .get("root")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &(input
            .get("exitCode")
            .and_then(Value::as_i64)
            .unwrap_or_default() as i32),
        &input
            .get("output")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        &input
            .get("timedOut")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
    ))
}

fn build_validate_test_timeout(input: &Value, _: &Context) -> Result<Value, VectorError> {
    castia::building::validate_test_timeout(
        input
            .get("timeout")
            .and_then(Value::as_f64)
            .unwrap_or_default(),
    )
    .map(|value| {
        if value.fract() == 0.0 {
            Value::from(value as i64)
        } else {
            Value::from(value)
        }
    })
    .map_err(vector_error)
}

fn delivery_validate_deployment_input(input: &Value, _: &Context) -> Result<Value, VectorError> {
    castia::delivery::validate_deployment_input(
        input
            .get("service")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input.get("environment").and_then(Value::as_str),
        input
            .get("projectEndpoint")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("projectResourceId")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("timeout")
            .and_then(Value::as_f64)
            .unwrap_or_default(),
    )
    .map_err(vector_error)
}

fn delivery_validate_environment_values(input: &Value, _: &Context) -> Result<Value, VectorError> {
    castia::delivery::validate_environment_values(
        input.get("values").unwrap_or(&Value::Null),
        input.get("requestedEnvironment").and_then(Value::as_str),
        input
            .get("projectEndpoint")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("projectResourceId")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("subscriptionId")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    )
    .map(Value::String)
    .map_err(vector_error)
}

fn delivery_verify_payload(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let payload = delivery_azd_to_snake(input.get("payload").unwrap_or(&Value::Null));
    castia::delivery::verify_payload(
        &payload,
        input
            .get("service")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("projectEndpoint")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input
            .get("projectResourceId")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        input.get("expectedModel").and_then(Value::as_str),
        input.get("expectedCandidate").and_then(Value::as_str),
        input.get("expectedVersion").and_then(Value::as_str),
    )
    .map_err(vector_error)
}

fn delivery_command_error_message(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let command = input
        .get("command")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect::<Vec<_>>();
    Ok(Value::String(
        CastiaDeliveryAzdRuntime.command_error_message(
            &command,
            &(input
                .get("returnCode")
                .and_then(Value::as_i64)
                .unwrap_or_default() as i32),
            &input
                .get("stdout")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            &input
                .get("stderr")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
        ),
    ))
}

fn delivery_manifest_publishable_protocols(
    input: &Value,
    _: &Context,
) -> Result<Value, VectorError> {
    Ok(json!(CastiaDeliveryManifestRuntime.publishable_protocols(
        &string_array_input(input, "registeredProtocols")?
    )))
}

fn delivery_manifest_skipped_protocols(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(json!(CastiaDeliveryManifestRuntime.skipped_protocols(
        &string_array_input(input, "registeredProtocols")?
    )))
}

fn delivery_manifest_service_protocol_items(
    input: &Value,
    _: &Context,
) -> Result<Value, VectorError> {
    Ok(CastiaDeliveryManifestRuntime
        .service_protocol_items(&string_array_input(input, "protocols")?))
}

fn delivery_manifest_plan(input: &Value, _: &Context) -> Result<Value, VectorError> {
    build_manifest_plan(
        &string_input(input, "appName")?,
        &string_array_input(input, "registeredProtocols")?,
        input.get("manifest").unwrap_or(&Value::Null),
        input
            .get("check")
            .and_then(Value::as_bool)
            .unwrap_or_default(),
    )
    .map_err(vector_error)
}

fn responses_input_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(serde_json::json!(
        CastiaResponsesRuntime.input_text(input.get("value").unwrap_or(&Value::Null))
    ))
}

fn responses_output_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let text = input
        .get("text")
        .and_then(Value::as_str)
        .unwrap_or_default();
    Ok(CastiaResponsesRuntime.output_body(&text.to_string()))
}

fn chat_last_user_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(serde_json::json!(CastiaChatRuntime.last_user_text(
        input.get("messages").unwrap_or(&Value::Null)
    )))
}

fn chat_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let text = input
        .get("text")
        .and_then(Value::as_str)
        .unwrap_or_default();
    Ok(CastiaChatRuntime.body(&text.to_string()))
}

fn invocations_input_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(serde_json::json!(
        CastiaInvocationsRuntime.input_text(input.get("body").unwrap_or(&Value::Null))
    ))
}

fn invocations_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let text = input
        .get("text")
        .and_then(Value::as_str)
        .unwrap_or_default();
    Ok(CastiaInvocationsRuntime.body(&text.to_string()))
}

fn normalize_generated_id(value: &Value, _: &Context) -> Value {
    let mut value = value.clone();
    if let Some(object) = value.as_object_mut() {
        object.remove("id");
    }
    value
}

fn normalize_agent_config_source(value: &Value, _: &Context) -> Value {
    let mut value = value.clone();
    if let Some(object) = value.as_object_mut() {
        if let Some(source) = object
            .get("source")
            .and_then(Value::as_str)
            .map(str::to_string)
        {
            if let Some(last) = source.rsplit(['/', '\\']).next() {
                if source.starts_with("local:") {
                    object.insert("source".to_string(), Value::String(format!("local:{last}")));
                }
            }
        }
    }
    value
}

fn normalize_special_json_keys(value: &Value, _: &Context) -> Value {
    strip_special_json_keys(value)
}

fn integrations_toolbox_to_camel(value: &Value, _: &Context) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match key.as_str() {
                        "server_label" => "serverLabel",
                        "server_url" => "serverUrl",
                        "require_approval" => "requireApproval",
                        "allowed_tools" => "allowedTools",
                        "project_connection_id" => "projectConnectionId",
                        "server_description" => "serverDescription",
                        "x-castia-optimizer-tool-definitions" => "optimizerToolDefinitions",
                        "x-castia-server-description" => "optimizerServerDescription",
                        _ => key,
                    };
                    (
                        translated.to_string(),
                        integrations_toolbox_to_camel(
                            value,
                            &Context {
                                contract: String::new(),
                                operation: String::new(),
                                vector: Value::Null,
                                provider: None,
                                target_api: None,
                                doubles: Value::Null,
                                base_dir: String::new(),
                            },
                        ),
                    )
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|value| {
                    integrations_toolbox_to_camel(
                        value,
                        &Context {
                            contract: String::new(),
                            operation: String::new(),
                            vector: Value::Null,
                            provider: None,
                            target_api: None,
                            doubles: Value::Null,
                            base_dir: String::new(),
                        },
                    )
                })
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn build_scaffold_to_camel(value: &Value, _: &Context) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match key.as_str() {
                        "app_target" => "appTarget",
                        "file_count" => "fileCount",
                        "content_digest" => "contentDigest",
                        "exit_code" => "exitCode",
                        "timed_out" => "timedOut",
                        _ => key,
                    };
                    (
                        translated.to_string(),
                        build_scaffold_to_camel(
                            value,
                            &Context {
                                contract: String::new(),
                                operation: String::new(),
                                vector: Value::Null,
                                provider: None,
                                target_api: None,
                                doubles: Value::Null,
                                base_dir: String::new(),
                            },
                        ),
                    )
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|value| {
                    build_scaffold_to_camel(
                        value,
                        &Context {
                            contract: String::new(),
                            operation: String::new(),
                            vector: Value::Null,
                            provider: None,
                            target_api: None,
                            doubles: Value::Null,
                            base_dir: String::new(),
                        },
                    )
                })
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn delivery_azd_to_camel(value: &Value, _: &Context) -> Value {
    observe_vector_numbers(&delivery_azd_keys(value, false))
}

fn delivery_azd_to_snake(value: &Value) -> Value {
    delivery_azd_keys(value, true)
}

fn delivery_azd_keys(value: &Value, to_snake: bool) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match (to_snake, key.as_str()) {
                        (true, "projectEndpoint") => "project_endpoint",
                        (true, "projectResourceId") => "project_resource_id",
                        (true, "subscriptionId") => "subscription_id",
                        (true, "environmentVariables") => "environment_variables",
                        (true, "codeConfiguration") => "code_configuration",
                        (true, "contentHash") => "content_hash",
                        (true, "expectedModel") => "expected_model",
                        (true, "expectedCandidate") => "expected_candidate",
                        (true, "expectedVersion") => "expected_version",
                        (true, "agentName") => "agent_name",
                        (true, "agentVersion") => "agent_version",
                        (true, "candidateId") => "candidate_id",
                        (true, "returnCode") => "return_code",
                        (false, "project_endpoint") => "projectEndpoint",
                        (false, "project_resource_id") => "projectResourceId",
                        (false, "subscription_id") => "subscriptionId",
                        (false, "environment_variables") => "environmentVariables",
                        (false, "code_configuration") => "codeConfiguration",
                        (false, "content_hash") => "contentHash",
                        (false, "expected_model") => "expectedModel",
                        (false, "expected_candidate") => "expectedCandidate",
                        (false, "expected_version") => "expectedVersion",
                        (false, "agent_name") => "agentName",
                        (false, "agent_version") => "agentVersion",
                        (false, "candidate_id") => "candidateId",
                        (false, "return_code") => "returnCode",
                        _ => key.as_str(),
                    };
                    (translated.to_string(), delivery_azd_keys(value, to_snake))
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|value| delivery_azd_keys(value, to_snake))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn lifecycle_record_to_camel(value: &Value, _: &Context) -> Value {
    lifecycle_keys(value, false)
}

fn lifecycle_record_to_snake(value: &Value) -> Value {
    lifecycle_keys(value, true)
}

fn observe_record_to_camel(value: &Value, _: &Context) -> Value {
    observe_vector_numbers(&observe_keys(value, false))
}

fn observe_record_to_snake(value: &Value) -> Value {
    observe_keys(value, true)
}

fn observe_suite_to_camel(value: &Value, _: &Context) -> Value {
    observe_suite_keys(value, false)
}

fn observe_tracing_to_camel(value: &Value, _: &Context) -> Value {
    observe_tracing_keys(value)
}

fn observe_telemetry_to_camel(value: &Value, _: &Context) -> Value {
    observe_vector_numbers(&observe_telemetry_keys(value, false))
}

fn observe_telemetry_to_snake(value: &Value) -> Value {
    observe_telemetry_keys(value, true)
}

fn observe_live_to_camel(value: &Value, _: &Context) -> Value {
    observe_vector_numbers(&observe_live_keys(value, false))
}

fn observe_live_to_snake(value: &Value) -> Value {
    observe_live_keys(value, true)
}

fn observe_live_keys(value: &Value, to_snake: bool) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match (to_snake, key.as_str()) {
                        (true, "maxRequests") => "max_requests",
                        (true, "maxSeconds") => "max_seconds",
                        (true, "maxOutputTokens") => "max_output_tokens",
                        (true, "maxTotalOutputTokens") => "max_total_output_tokens",
                        (true, "maxResponseBytes") => "max_response_bytes",
                        (true, "projectEndpoint") => "project_endpoint",
                        (true, "projectReadUrl") => "project_read_url",
                        (true, "modelUrl") => "model_url",
                        (true, "hostedResponsesUrl") => "hosted_responses_url",
                        (true, "hostedInvocationsUrl") => "hosted_invocations_url",
                        (true, "toolboxUrl") => "toolbox_url",
                        (true, "toolboxTools") => "toolbox_tools",
                        (true, "projectReadScope") => "project_read_scope",
                        (true, "hostedScope") => "hosted_scope",
                        (true, "allowOptimizerSubmit") => "allow_optimizer_submit",
                        (true, "optimizerRequest") => "optimizer_request",
                        (true, "optimizerTimeoutSeconds") => "optimizer_timeout_seconds",
                        (true, "optimizerPollSeconds") => "optimizer_poll_seconds",
                        (true, "cleanupTimeoutSeconds") => "cleanup_timeout_seconds",
                        (true, "maxCandidates") => "max_candidates",
                        (true, "hostedResponsesBody") => "hosted_responses_body",
                        (true, "hostedResponsesExpectedText") => "hosted_responses_expected_text",
                        (true, "outputText") => "output_text",
                        (true, "inputTokens") => "input_tokens",
                        (true, "outputTokens") => "output_tokens",
                        (true, "totalTokens") => "total_tokens",
                        (true, "outputCharacters") => "output_characters",
                        (true, "deltaCount") => "delta_count",
                        (true, "completedCount") => "completed_count",
                        (true, "operationId") => "operationId",
                        (true, "systemPrompt") => "system_prompt",
                        (false, "max_requests") => "maxRequests",
                        (false, "max_seconds") => "maxSeconds",
                        (false, "max_output_tokens") => "maxOutputTokens",
                        (false, "max_total_output_tokens") => "maxTotalOutputTokens",
                        (false, "max_response_bytes") => "maxResponseBytes",
                        (false, "project_endpoint") => "projectEndpoint",
                        (false, "project_read_url") => "projectReadUrl",
                        (false, "model_url") => "modelUrl",
                        (false, "hosted_responses_url") => "hostedResponsesUrl",
                        (false, "hosted_invocations_url") => "hostedInvocationsUrl",
                        (false, "toolbox_url") => "toolboxUrl",
                        (false, "toolbox_tools") => "toolboxTools",
                        (false, "project_read_scope") => "projectReadScope",
                        (false, "hosted_scope") => "hostedScope",
                        (false, "allow_optimizer_submit") => "allowOptimizerSubmit",
                        (false, "optimizer_request") => "optimizerRequest",
                        (false, "optimizer_timeout_seconds") => "optimizerTimeoutSeconds",
                        (false, "optimizer_poll_seconds") => "optimizerPollSeconds",
                        (false, "cleanup_timeout_seconds") => "cleanupTimeoutSeconds",
                        (false, "max_candidates") => "maxCandidates",
                        (false, "hosted_responses_body") => "hostedResponsesBody",
                        (false, "hosted_responses_expected_text") => "hostedResponsesExpectedText",
                        (false, "output_text") => "outputText",
                        (false, "input_tokens") => "inputTokens",
                        (false, "output_tokens") => "outputTokens",
                        (false, "total_tokens") => "totalTokens",
                        (false, "output_characters") => "outputCharacters",
                        (false, "delta_count") => "deltaCount",
                        (false, "completed_count") => "completedCount",
                        (false, "system_prompt") => "systemPrompt",
                        _ => key.as_str(),
                    };
                    (translated.to_string(), observe_live_keys(value, to_snake))
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|value| observe_live_keys(value, to_snake))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn observe_telemetry_keys(value: &Value, to_snake: bool) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match (to_snake, key.as_str()) {
                        (true, "agentName") => "agent_name",
                        (true, "agentVersion") => "agent_version",
                        (true, "probeTag") => "probe_tag",
                        (true, "includeContent") => "include_content",
                        (true, "traceId") => "trace_id",
                        (true, "timeoutSeconds") => "timeout_seconds",
                        (true, "pollSeconds") => "poll_seconds",
                        (true, "maxAttempts") => "max_attempts",
                        (true, "matchedRecords") => "matched_records",
                        (true, "elapsedSeconds") => "elapsed_seconds",
                        (true, "statusCode") => "status_code",
                        (false, "agent_name") => "agentName",
                        (false, "agent_version") => "agentVersion",
                        (false, "probe_tag") => "probeTag",
                        (false, "include_content") => "includeContent",
                        (false, "trace_id") => "traceId",
                        (false, "timeout_seconds") => "timeoutSeconds",
                        (false, "poll_seconds") => "pollSeconds",
                        (false, "max_attempts") => "maxAttempts",
                        (false, "matched_records") => "matchedRecords",
                        (false, "elapsed_seconds") => "elapsedSeconds",
                        (false, "status_code") => "statusCode",
                        _ => key.as_str(),
                    };
                    (
                        translated.to_string(),
                        observe_telemetry_keys(value, to_snake),
                    )
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|value| observe_telemetry_keys(value, to_snake))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn observe_tracing_keys(value: &Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match key.as_str() {
                        "tracer_name" => "tracerName",
                        "span_name" => "spanName",
                        _ => key,
                    };
                    (translated.to_string(), observe_tracing_keys(value))
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(observe_tracing_keys).collect()),
        _ => value.clone(),
    }
}

fn observe_suite_to_snake(value: &Value) -> Value {
    observe_suite_keys(value, true)
}

fn observe_suite_keys(value: &Value, to_snake: bool) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = if to_snake {
                        suite_key_to_snake(key).unwrap_or_else(|| key.clone())
                    } else {
                        suite_key_to_camel(key)
                    };
                    (translated.to_string(), observe_suite_keys(value, to_snake))
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|value| observe_suite_keys(value, to_snake))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn suite_feature_key(key: &str) -> String {
    suite_key_to_snake(key)
        .filter(|value| value.contains('.'))
        .unwrap_or_else(|| key.to_string())
}

fn suite_key_to_snake(key: &str) -> Option<String> {
    let mapped = match key {
        "schemaVersion" => "schema_version",
        "generatedAt" => "generated_at",
        "featureId" => "feature_id",
        "durationSeconds" => "duration_seconds",
        "costStatus" => "cost_status",
        "lostCoverage" => "lost_coverage",
        "newCoverage" => "new_coverage",
        _ => "",
    };
    if !mapped.is_empty() {
        return Some(mapped.to_string());
    }
    let catalog = CastiaObserveSuiteRuntime.feature_catalog();
    let empty_features = Vec::new();
    for feature in catalog["features"].as_array().unwrap_or(&empty_features) {
        if let Some(id) = feature["id"].as_str() {
            if key == snake_like_to_camel(id, '.') {
                return Some(id.to_string());
            }
        }
        let empty_prerequisites = Vec::new();
        for prerequisite in feature["prerequisites"]
            .as_array()
            .unwrap_or(&empty_prerequisites)
        {
            if let Some(prerequisite) = prerequisite.as_str() {
                if key == snake_like_to_camel(prerequisite, '_') {
                    return Some(prerequisite.to_string());
                }
            }
        }
    }
    None
}

fn suite_key_to_camel(key: &str) -> String {
    match key {
        "schema_version" => "schemaVersion".to_string(),
        "generated_at" => "generatedAt".to_string(),
        "feature_id" => "featureId".to_string(),
        "duration_seconds" => "durationSeconds".to_string(),
        "cost_status" => "costStatus".to_string(),
        "lost_coverage" => "lostCoverage".to_string(),
        "new_coverage" => "newCoverage".to_string(),
        other if other.contains('.') => snake_like_to_camel(other, '.'),
        other if other.contains('_') => snake_like_to_camel(other, '_'),
        other => other.to_string(),
    }
}

fn snake_like_to_camel(value: &str, separator: char) -> String {
    let mut parts = value.split(separator);
    let Some(first) = parts.next() else {
        return String::new();
    };
    let mut output = first.to_string();
    for part in parts {
        let mut chars = part.chars();
        if let Some(first) = chars.next() {
            output.extend(first.to_uppercase());
            output.extend(chars);
        }
    }
    output
}

fn observe_keys(value: &Value, to_snake: bool) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match (to_snake, key.as_str()) {
                        (true, "agentName") => "agent_name",
                        (true, "agentVersion") => "agent_version",
                        (true, "traceId") => "trace_id",
                        (true, "spanId") => "span_id",
                        (true, "parentId") => "parent_id",
                        (true, "latencyMs") => "latency_ms",
                        (true, "inputTokens") => "input_tokens",
                        (true, "outputTokens") => "output_tokens",
                        (true, "totalTokens") => "total_tokens",
                        (true, "toolName") => "tool_name",
                        (true, "probeTag") => "probe_tag",
                        (true, "recordCount") => "record_count",
                        (true, "errorCount") => "error_count",
                        (true, "observedErrorCount") => "observed_error_count",
                        (true, "knownStatusCount") => "known_status_count",
                        (true, "unknownStatusCount") => "unknown_status_count",
                        (true, "errorRate") => "error_rate",
                        (true, "errorRateDenominator") => "error_rate_denominator",
                        (true, "toolErrors") => "tool_errors",
                        (true, "toolStatusCounts") => "tool_status_counts",
                        (true, "knownRecords") => "known_records",
                        (true, "unknownRecords") => "unknown_records",
                        (true, "observedSum") => "observed_sum",
                        (true, "costStatus") => "cost_status",
                        (true, "unknownCount") => "unknown_count",
                        (false, "agent_name") => "agentName",
                        (false, "agent_version") => "agentVersion",
                        (false, "trace_id") => "traceId",
                        (false, "span_id") => "spanId",
                        (false, "parent_id") => "parentId",
                        (false, "latency_ms") => "latencyMs",
                        (false, "input_tokens") => "inputTokens",
                        (false, "output_tokens") => "outputTokens",
                        (false, "total_tokens") => "totalTokens",
                        (false, "tool_name") => "toolName",
                        (false, "probe_tag") => "probeTag",
                        (false, "record_count") => "recordCount",
                        (false, "error_count") => "errorCount",
                        (false, "observed_error_count") => "observedErrorCount",
                        (false, "known_status_count") => "knownStatusCount",
                        (false, "unknown_status_count") => "unknownStatusCount",
                        (false, "error_rate") => "errorRate",
                        (false, "error_rate_denominator") => "errorRateDenominator",
                        (false, "tool_errors") => "toolErrors",
                        (false, "tool_status_counts") => "toolStatusCounts",
                        (false, "known_records") => "knownRecords",
                        (false, "unknown_records") => "unknownRecords",
                        (false, "observed_sum") => "observedSum",
                        (false, "cost_status") => "costStatus",
                        (false, "unknown_count") => "unknownCount",
                        _ => key.as_str(),
                    };
                    let translated_value = if matches!(
                        translated,
                        "toolErrors"
                            | "toolStatusCounts"
                            | "content"
                            | "tool_errors"
                            | "tool_status_counts"
                    ) {
                        value.clone()
                    } else {
                        observe_keys(value, to_snake)
                    };
                    (translated.to_string(), translated_value)
                })
                .collect(),
        ),
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| observe_keys(v, to_snake)).collect())
        }
        _ => value.clone(),
    }
}

fn observe_vector_numbers(value: &Value) -> Value {
    match value {
        Value::Number(number) => number
            .as_f64()
            .filter(|number| number.is_finite() && number.fract() == 0.0)
            .map(|number| json!(number as i64))
            .unwrap_or_else(|| value.clone()),
        Value::Array(items) => Value::Array(items.iter().map(observe_vector_numbers).collect()),
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| (key.clone(), observe_vector_numbers(value)))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn lifecycle_keys(value: &Value, to_snake: bool) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let translated = match (to_snake, key.as_str()) {
                        (true, "schemaVersion") => "schema_version",
                        (true, "sourceFiles") => "source_files",
                        (true, "referenceOrigins") => "reference_origins",
                        (true, "trainIds") => "train_ids",
                        (true, "heldoutIds") => "heldout_ids",
                        (true, "redactionVersion") => "redaction_version",
                        (true, "exampleId") => "example_id",
                        (true, "agentId") => "agent_id",
                        (true, "datasetId") => "dataset_id",
                        (true, "expectedIds") => "expected_ids",
                        (true, "baselineId") => "baseline_id",
                        (true, "agentSnapshot") => "agent_snapshot",
                        (true, "baselineRunId") => "baseline_run_id",
                        (true, "candidateRunId") => "candidate_run_id",
                        (true, "baselineAgentId") => "baseline_agent_id",
                        (true, "candidateAgentId") => "candidate_agent_id",
                        (true, "traceId") => "trace_id",
                        (true, "referenceOrigin") => "reference_origin",
                        (true, "modelOutput") => "model_output",
                        (true, "minimumQuality") => "minimum_quality",
                        (true, "maximumQualityDrop") => "maximum_quality_drop",
                        (true, "maximumExampleRegressions") => "maximum_example_regressions",
                        (true, "maximumLatencySeconds") => "maximum_latency_seconds",
                        (true, "maximumCost") => "maximum_cost",
                        (true, "requiredMetrics") => "required_metrics",
                        (true, "requireHeldout") => "require_heldout",
                        (true, "knownGood") => "known_good",
                        (true, "previousGood") => "previous_good",
                        (true, "lastStatus") => "last_status",
                        (true, "remoteState") => "remote_state",
                        (true, "pendingCleanup") => "pending_cleanup",
                        (false, "schema_version") => "schemaVersion",
                        (false, "source_files") => "sourceFiles",
                        (false, "reference_origins") => "referenceOrigins",
                        (false, "train_ids") => "trainIds",
                        (false, "heldout_ids") => "heldoutIds",
                        (false, "redaction_version") => "redactionVersion",
                        (false, "example_id") => "exampleId",
                        (false, "agent_id") => "agentId",
                        (false, "dataset_id") => "datasetId",
                        (false, "expected_ids") => "expectedIds",
                        (false, "baseline_id") => "baselineId",
                        (false, "agent_snapshot") => "agentSnapshot",
                        (false, "baseline_run_id") => "baselineRunId",
                        (false, "candidate_run_id") => "candidateRunId",
                        (false, "baseline_agent_id") => "baselineAgentId",
                        (false, "candidate_agent_id") => "candidateAgentId",
                        (false, "trace_id") => "traceId",
                        (false, "reference_origin") => "referenceOrigin",
                        (false, "model_output") => "modelOutput",
                        (false, "minimum_quality") => "minimumQuality",
                        (false, "maximum_quality_drop") => "maximumQualityDrop",
                        (false, "maximum_example_regressions") => "maximumExampleRegressions",
                        (false, "maximum_latency_seconds") => "maximumLatencySeconds",
                        (false, "maximum_cost") => "maximumCost",
                        (false, "required_metrics") => "requiredMetrics",
                        (false, "require_heldout") => "requireHeldout",
                        (false, "known_good") => "knownGood",
                        (false, "previous_good") => "previousGood",
                        (false, "last_status") => "lastStatus",
                        (false, "remote_state") => "remoteState",
                        (false, "pending_cleanup") => "pendingCleanup",
                        _ => key.as_str(),
                    };
                    let value = if matches!(
                        translated,
                        "dependencies"
                            | "model"
                            | "tools"
                            | "metrics"
                            | "configuration"
                            | "aggregates"
                    ) {
                        value.clone()
                    } else {
                        lifecycle_keys(value, to_snake)
                    };
                    (translated.to_string(), value)
                })
                .collect(),
        ),
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| lifecycle_keys(v, to_snake)).collect())
        }
        _ => value.clone(),
    }
}

fn strip_special_json_keys(value: &Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .filter(|(key, _)| key.as_str() != "$schema" && !key.starts_with('@'))
                .map(|(key, value)| (key.clone(), strip_special_json_keys(value)))
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(strip_special_json_keys).collect()),
        _ => value.clone(),
    }
}

fn restore_private_tool_keys(value: &Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(key, value)| {
                    let key = if key == "xCastiaOptimizerToolDefinitions" {
                        "x-castia-optimizer-tool-definitions".to_string()
                    } else {
                        key.clone()
                    };
                    (key, restore_private_tool_keys(value))
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(restore_private_tool_keys).collect()),
        _ => value.clone(),
    }
}

fn save_optimizer_env() -> Vec<(&'static str, Option<String>)> {
    OPTIMIZER_ENV
        .iter()
        .map(|key| (*key, env::var(key).ok()))
        .collect()
}

fn restore_optimizer_env(saved: Vec<(&'static str, Option<String>)>) {
    for key in OPTIMIZER_ENV {
        env::remove_var(key);
    }
    for (key, value) in saved {
        if let Some(value) = value {
            env::set_var(key, value);
        }
    }
}

fn apply_vector_env(vector: &Value) {
    for key in OPTIMIZER_ENV {
        env::remove_var(key);
    }
    let Some(env_values) = vector.get("env").and_then(Value::as_object) else {
        return;
    };
    for (key, value) in env_values {
        if let Some(value) = value.as_str() {
            env::set_var(key, value);
        }
    }
}

fn write_vector_files(root: &Path, vector: &Value) -> Result<(), VectorError> {
    let Some(files) = vector.get("files").and_then(Value::as_array) else {
        return Ok(());
    };
    for file in files {
        let Some(relative) = file.get("path").and_then(Value::as_str) else {
            continue;
        };
        let Some(contents) = file.get("content").and_then(Value::as_str) else {
            continue;
        };
        let path = root.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(io_error)?;
        }
        fs::write(path, contents).map_err(io_error)?;
    }
    Ok(())
}

fn replace_temp_path(value: &mut Value, temp: &str) {
    match value {
        Value::String(value) if value == temp => *value = "$temp".to_string(),
        Value::String(value) => {
            let normalized = value.replace('\\', "/");
            let temp = temp.replace('\\', "/");
            if normalized.starts_with(&temp) {
                *value = normalized.replacen(&temp, "$temp", 1);
            }
        }
        Value::Array(items) => {
            for item in items {
                replace_temp_path(item, temp);
            }
        }
        Value::Object(object) => {
            for value in object.values_mut() {
                replace_temp_path(value, temp);
            }
        }
        _ => {}
    }
}

fn vector_path(input: &Value, key: &str, temp: &Path) -> Result<String, VectorError> {
    let Some(path) = input.get(key).and_then(Value::as_str) else {
        return Err(VectorError {
            message: format!("missing {key} input"),
            payload: None,
        });
    };
    if path == "$temp" {
        return Ok(temp.to_string_lossy().to_string());
    }
    if let Some(rest) = path
        .strip_prefix("$temp/")
        .or_else(|| path.strip_prefix("$temp\\"))
    {
        return Ok(temp.join(rest).to_string_lossy().to_string());
    }
    Ok(path.to_string())
}

fn required<'a>(input: &'a Value, key: &str) -> Result<&'a Value, VectorError> {
    input.get(key).ok_or_else(|| VectorError {
        message: format!("missing {key} input"),
        payload: None,
    })
}

fn string_input(input: &Value, key: &str) -> Result<String, VectorError> {
    required(input, key)?
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| VectorError {
            message: format!("{key} must be a string"),
            payload: None,
        })
}

fn string_array_input(input: &Value, key: &str) -> Result<Vec<String>, VectorError> {
    required(input, key)?
        .as_array()
        .ok_or_else(|| VectorError {
            message: format!("{key} must be a string array"),
            payload: None,
        })?
        .iter()
        .map(|item| {
            item.as_str()
                .map(str::to_string)
                .ok_or_else(|| VectorError {
                    message: format!("{key} must be a string array"),
                    payload: None,
                })
        })
        .collect()
}

fn vector_temp_label(ctx: &Context) -> String {
    format!(
        "{}-{}",
        ctx.operation,
        ctx.vector
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("unnamed")
    )
}

fn temp_dir(label: &str) -> PathBuf {
    let root = env::temp_dir().join(format!(
        "castia-vector-{}-{}",
        label.replace(|c: char| !c.is_ascii_alphanumeric(), "_"),
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).expect("vector temp dir created");
    root
}

fn io_error(error: std::io::Error) -> VectorError {
    VectorError {
        message: error.to_string(),
        payload: None,
    }
}
