use castia::evaluation::CastiaEvaluationSuiteRuntime;
use castia::inference::{try_reasoning_param, CastiaModelRuntime, CastiaToolCatalogRuntime};
use castia::lifecycle::{canonical_json, check_public, content_hash, safe_path};
use castia::messaging::{
    try_require_agentic_user, CastiaCardsRuntime, CastiaEntitiesRuntime, CastiaIdentityRuntime,
    CastiaInvokesRuntime, CastiaRoutingRuntime,
};
use castia::model::{
    Activity, ActivityRuntime, AgentConfigResolver, CardsRuntime, ChatRuntime, EntitiesRuntime,
    EvaluationSuiteRuntime, IdentityRuntime, InvocationsRuntime, InvokesRuntime,
    LifecycleRecordsRuntime, LoadContext, ModelRuntime, ResponsesRuntime, RoutingRuntime,
    SaveContext, ToolCatalogRuntime,
};
use castia::optimizing::CastiaAgentConfigResolver;
use castia::protocols::{
    CastiaActivityRuntime, CastiaChatRuntime, CastiaInvocationsRuntime, CastiaResponsesRuntime,
};
use serde_json::Value;
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
            "IdentityRuntime.agenticUserId",
            sync(identity_agentic_user_id),
        ),
        (
            "IdentityRuntime.requireAgenticUser",
            sync(identity_require_agentic_user),
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
            "LifecycleRecordsRuntime.safePath",
            sync(lifecycle_safe_path),
        ),
        (
            "ModelRuntime.instructionsParam",
            sync(model_instructions_param),
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

fn vector_error(error: impl std::fmt::Display) -> VectorError {
    VectorError {
        message: error.to_string(),
        payload: None,
    }
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
