use castia::model::{AgentConfigResolver, SaveContext};
use castia::optimizing::CastiaAgentConfigResolver;
use serde_json::Value;
use std::env;
use std::fs;
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::{Mutex, OnceLock};

static ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

const OPTIMIZER_ENV: &[&str] = &[
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "OPTIMIZATION_CONFIG",
    "OPTIMIZATION_CANDIDATE_ID",
    "OPTIMIZATION_RESOLVE_ENDPOINT",
    "OPTIMIZATION_LOCAL_DIR",
];

#[test]
fn candidate_precedence_fixture_matches_rust_resolution_contract() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .expect("packages/rust has a repo root parent")
        .to_path_buf();
    let fixture_path = root
        .join("spec")
        .join("conformance")
        .join("optimization")
        .join("candidate_precedence.json");
    let fixture: Value = serde_json::from_str(
        &fs::read_to_string(fixture_path).expect("candidate precedence fixture exists"),
    )
    .expect("candidate precedence fixture parses");

    let ids: Vec<_> = fixture["precedence"]
        .as_array()
        .expect("precedence is an array")
        .iter()
        .map(|entry| entry["id"].as_str().expect("id is a string"))
        .collect();
    assert_eq!(
        ids,
        vec![
            "inline_config",
            "resolver_api",
            "local_agent_configs",
            "none"
        ]
    );
    let affected: Vec<_> = fixture["precedence"]
        .as_array()
        .expect("precedence is an array")
        .iter()
        .filter(|entry| entry["affected_by_config_dir"].as_bool().unwrap_or(false))
        .map(|entry| entry["id"].as_str().expect("id is a string"))
        .collect();
    assert_eq!(affected, vec!["local_agent_configs"]);
}

#[tokio::test]
async fn inline_config_wins_over_local_and_uses_env_source() {
    let _guard = env_guard();
    let temp = temp_root("inline_config_wins");
    write_baseline(&temp, "gpt-local", "local instructions", None);
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");
    env::set_var(
        "OPTIMIZATION_CONFIG",
        r#"{"model":"gpt-inline","instructions":"inline instructions","toolDefinitions":[{"function":{"name":"web"}}]}"#,
    );

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.source, "env:OPTIMIZATION_CONFIG");
    assert_eq!(resolution.model.as_deref(), Some("gpt-inline"));
    assert_eq!(
        resolution.instructions.as_deref(),
        Some("inline instructions")
    );
    assert_eq!(resolution.tool_definitions[0]["function"]["name"], "web");
}

#[tokio::test]
async fn resolver_api_env_is_terminal_and_does_not_fall_through_to_local() {
    let _guard = env_guard();
    let temp = temp_root("resolver_api_terminal");
    write_baseline(&temp, "gpt-local", "local instructions", None);
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");
    env::set_var("OPTIMIZATION_CANDIDATE_ID", "candidate-1");
    env::set_var(
        "OPTIMIZATION_RESOLVE_ENDPOINT",
        "https://example.invalid/resolve",
    );

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.source, "unsupported:resolver_api");
    assert_eq!(resolution.model.as_deref(), Some("gpt-env"));
    assert_eq!(resolution.instructions, None);
}

#[tokio::test]
async fn local_candidate_id_wins_over_baseline_with_tools_file_alias() {
    let _guard = env_guard();
    let temp = temp_root("local_candidate_wins");
    write_baseline(&temp, "gpt-baseline", "baseline instructions", None);
    let candidate = temp.join("candidate-1");
    fs::create_dir_all(&candidate).expect("candidate dir created");
    fs::write(
        candidate.join("metadata.yaml"),
        "model: gpt-candidate\ninstruction_file: instructions.md\ntools_file: tools.json\n",
    )
    .expect("metadata written");
    fs::write(candidate.join("instructions.md"), "candidate instructions")
        .expect("instructions written");
    fs::write(
        candidate.join("tools.json"),
        r#"[{"type":"function","function":{"name":"web","description":"Search."}}]"#,
    )
    .expect("tools written");
    env::set_var("OPTIMIZATION_CANDIDATE_ID", "candidate-1");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert!(resolution.source.ends_with("candidate-1"));
    assert_eq!(resolution.model.as_deref(), Some("gpt-candidate"));
    assert_eq!(
        resolution.instructions.as_deref(),
        Some("candidate instructions")
    );
    assert_eq!(resolution.tool_definitions[0]["function"]["name"], "web");
}

#[tokio::test]
async fn local_baseline_uses_config_dir_and_defaults_missing_model() {
    let _guard = env_guard();
    let temp = temp_root("local_baseline_config_dir");
    write_baseline(&temp, "", "baseline instructions", None);
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert!(resolution.source.ends_with("baseline"));
    assert_eq!(resolution.model.as_deref(), Some("gpt-env"));
    assert_eq!(
        resolution.instructions.as_deref(),
        Some("baseline instructions")
    );
}

#[tokio::test]
async fn missing_sources_degrade_to_env_defaults() {
    let _guard = env_guard();
    let temp = temp_root("missing_sources");
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");
    env::set_var("OPTIMIZATION_LOCAL_DIR", temp.path().as_os_str());

    let resolution = CastiaAgentConfigResolver
        .resolve(&None, &AtomicBool::new(false))
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.source, "default");
    assert_eq!(resolution.model.as_deref(), Some("gpt-env"));
    assert_eq!(resolution.instructions, None);
    assert_eq!(
        resolution.to_value(&SaveContext::default()),
        serde_json::json!({"source":"default","model":"gpt-env"})
    );
}

#[tokio::test]
async fn malformed_inline_config_is_terminal_and_degrades_to_defaults() {
    let _guard = env_guard();
    let temp = temp_root("malformed_inline");
    write_baseline(&temp, "gpt-local", "local instructions", None);
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");
    env::set_var("OPTIMIZATION_CONFIG", "{not-json");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.source, "default");
    assert_eq!(resolution.model.as_deref(), Some("gpt-env"));
    assert_eq!(resolution.instructions, None);
}

#[tokio::test]
async fn missing_requested_local_candidate_does_not_fall_back_to_baseline() {
    let _guard = env_guard();
    let temp = temp_root("missing_local_candidate");
    write_baseline(&temp, "gpt-local", "local instructions", None);
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");
    env::set_var("OPTIMIZATION_CANDIDATE_ID", "missing-candidate");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.source, "default");
    assert_eq!(resolution.model.as_deref(), Some("gpt-env"));
    assert_eq!(resolution.instructions, None);
}

#[tokio::test]
async fn malformed_local_metadata_degrades_to_defaults() {
    let _guard = env_guard();
    let temp = temp_root("malformed_local_metadata");
    fs::create_dir_all(temp.join("baseline")).expect("baseline dir created");
    fs::write(temp.join("baseline").join("metadata.yaml"), "model: [").expect("metadata written");
    env::set_var("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.source, "default");
    assert_eq!(resolution.model.as_deref(), Some("gpt-env"));
    assert_eq!(resolution.instructions, None);
}

#[tokio::test]
async fn resolver_api_requires_both_env_vars_before_terminal_fallback() {
    let _guard = env_guard();
    let temp = temp_root("resolver_requires_both");
    write_baseline(&temp, "gpt-local", "local instructions", None);
    env::set_var(
        "OPTIMIZATION_RESOLVE_ENDPOINT",
        "https://example.invalid/resolve",
    );

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert!(resolution.source.ends_with("baseline"));
    assert_eq!(resolution.model.as_deref(), Some("gpt-local"));
    assert_eq!(
        resolution.instructions.as_deref(),
        Some("local instructions")
    );
}

#[tokio::test]
async fn optimization_local_dir_is_used_only_when_config_dir_is_absent() {
    let _guard = env_guard();
    let env_root = temp_root("local_dir_env");
    let explicit_root = temp_root("local_dir_explicit");
    write_baseline(&env_root, "gpt-env-root", "env root instructions", None);
    write_baseline(
        &explicit_root,
        "gpt-explicit-root",
        "explicit root instructions",
        None,
    );
    env::set_var("OPTIMIZATION_LOCAL_DIR", env_root.path().as_os_str());

    let from_env = CastiaAgentConfigResolver
        .resolve(&None, &AtomicBool::new(false))
        .await
        .expect("env-root resolution succeeds");
    let from_explicit = CastiaAgentConfigResolver
        .resolve(
            &Some(explicit_root.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("explicit-root resolution succeeds");

    assert_eq!(from_env.model.as_deref(), Some("gpt-env-root"));
    assert_eq!(from_explicit.model.as_deref(), Some("gpt-explicit-root"));
}

#[tokio::test]
async fn local_skills_are_composed_into_instructions() {
    let _guard = env_guard();
    let temp = temp_root("local_skills");
    write_baseline(&temp, "gpt-local", "base instructions", None);
    fs::write(
        temp.join("baseline").join("metadata.yaml"),
        "model: gpt-local\ninstruction_file: instructions.md\nskill_dir: skills\n",
    )
    .expect("metadata written");
    fs::create_dir_all(temp.join("baseline").join("skills").join("tone"))
        .expect("skill dir written");
    fs::write(
        temp.join("baseline")
            .join("skills")
            .join("tone")
            .join("SKILL.md"),
        "be concise",
    )
    .expect("skill written");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(
        resolution.instructions.as_deref(),
        Some("base instructions\n\nbe concise")
    );
}

#[tokio::test]
async fn tools_file_alias_is_used_when_tool_file_is_invalid() {
    let _guard = env_guard();
    let temp = temp_root("tools_file_alias");
    write_baseline(&temp, "gpt-local", "base instructions", None);
    let baseline = temp.join("baseline");
    fs::write(
        baseline.join("metadata.yaml"),
        "model: gpt-local\ninstruction_file: instructions.md\ntool_file: missing.json\ntools_file: tools.json\n",
    )
    .expect("metadata written");
    fs::write(
        baseline.join("tools.json"),
        r#"[{"type":"function","function":{"name":"search"}}]"#,
    )
    .expect("tools written");

    let resolution = CastiaAgentConfigResolver
        .resolve(
            &Some(temp.path().to_string_lossy().to_string()),
            &AtomicBool::new(false),
        )
        .await
        .expect("resolution succeeds");

    assert_eq!(resolution.tool_definitions[0]["function"]["name"], "search");
}

fn env_guard() -> std::sync::MutexGuard<'static, ()> {
    let guard = ENV_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    for key in OPTIMIZER_ENV {
        env::remove_var(key);
    }
    guard
}

struct TestDir(PathBuf);

impl TestDir {
    fn path(&self) -> &Path {
        &self.0
    }
}

impl Deref for TestDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl Drop for TestDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn temp_root(name: &str) -> TestDir {
    let root = env::temp_dir().join(format!("castia-rust-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).expect("temp root created");
    TestDir(root)
}

fn write_baseline(root: &Path, model: &str, instructions: &str, tool_key: Option<&str>) {
    let baseline = root.join("baseline");
    fs::create_dir_all(&baseline).expect("baseline dir created");
    let mut metadata = String::new();
    if !model.is_empty() {
        metadata.push_str(&format!("model: {model}\n"));
    }
    metadata.push_str("instruction_file: instructions.md\n");
    if let Some(tool_key) = tool_key {
        metadata.push_str(&format!("{tool_key}: tools.json\n"));
    }
    fs::write(baseline.join("metadata.yaml"), metadata).expect("metadata written");
    fs::write(baseline.join("instructions.md"), instructions).expect("instructions written");
}
