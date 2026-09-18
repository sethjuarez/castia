use crate::model::ObserveSuiteRuntime;
use chrono::{DateTime, SecondsFormat, Utc};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

const STATUSES: [&str; 4] = ["blocked", "fail", "pass", "uncovered"];
const BLOCKING_CATEGORIES: [&str; 5] = [
    "authentication",
    "authorization",
    "budget",
    "dependency",
    "prerequisite",
];
const INVALID_RESPONSE_DIAGNOSTIC: &str = "The service response did not match its contract.";
const UNEXPECTED_DIAGNOSTIC: &str = "The probe raised an unexpected exception.";
const FEATURE_CATALOG: [(&str, &str, &[&str]); 37] = [
    (
        "project.read",
        "Read the selected Foundry project",
        &["project_read_url"],
    ),
    (
        "runtime.responses",
        "Hosted Responses protocol end-to-end",
        &["hosted_responses_url"],
    ),
    (
        "runtime.invocations",
        "Hosted Invocations protocol end-to-end",
        &[
            "hosted_invocations_url",
            "hosted_invocations_body",
            "hosted_invocations_output_field",
        ],
    ),
    (
        "runtime.activity",
        "Authenticated Activity protocol delivery",
        &["activity_fixture"],
    ),
    (
        "runtime.routing",
        "Router composition and dispatch",
        &["runtime_fixture"],
    ),
    (
        "runtime.dependencies",
        "Dependency injection and request context",
        &["runtime_fixture"],
    ),
    (
        "runtime.errors",
        "Runtime error boundaries",
        &["runtime_fixture"],
    ),
    (
        "teams.direct",
        "Teams direct message delivery",
        &["teams_fixture"],
    ),
    ("teams.mention", "Teams mention routing", &["teams_fixture"]),
    (
        "teams.conversation",
        "Conversation lifecycle delivery",
        &["teams_fixture"],
    ),
    (
        "teams.streaming",
        "Teams streaming message updates",
        &["teams_fixture"],
    ),
    (
        "teams.rich",
        "Adaptive cards and rich message rendering",
        &["teams_rich_fixture"],
    ),
    (
        "teams.actions",
        "Card submit and invoke actions",
        &["teams_rich_fixture"],
    ),
    (
        "teams.proactive",
        "Proactive Teams delivery",
        &["teams_proactive_fixture"],
    ),
    (
        "identity.authentication",
        "Incoming token validation",
        &["identity_fixture"],
    ),
    (
        "identity.authorization",
        "Caller authorization boundaries",
        &["identity_fixture"],
    ),
    (
        "identity.obo",
        "Delegated on-behalf-of token acquisition",
        &["obo_fixture"],
    ),
    (
        "graph.read",
        "Delegated Microsoft Graph read",
        &["graph_fixture"],
    ),
    (
        "graph.write",
        "Explicitly authorized Graph write fixture",
        &["graph_write_fixture"],
    ),
    (
        "model.respond",
        "Foundry model Responses text",
        &["model_url", "model"],
    ),
    (
        "model.stream",
        "Foundry model Responses streaming",
        &["model_url", "model"],
    ),
    (
        "model.function_call",
        "Forced local function call and output round-trip",
        &["model_url", "model"],
    ),
    (
        "model.reasoning",
        "Reasoning model configuration",
        &["reasoning_fixture"],
    ),
    (
        "tools.execution",
        "Local tool execution and failure handling",
        &["tool_fixture"],
    ),
    (
        "tools.toolbox",
        "Explicit selected toolbox MCP tool execution",
        &[
            "model_url",
            "model",
            "toolbox_url",
            "toolbox_tools",
            "toolbox_prompt",
        ],
    ),
    (
        "tools.knowledge",
        "Knowledge-base retrieval",
        &["knowledge_fixture"],
    ),
    (
        "telemetry.query",
        "Scoped Application Insights trace query",
        &["app_insights"],
    ),
    (
        "telemetry.ingestion",
        "Tagged probe trace ingestion",
        &["app_insights", "probe_tag"],
    ),
    (
        "eval.check",
        "Offline evaluation schema validation",
        &["eval_fixture"],
    ),
    (
        "eval.run",
        "Bounded live evaluation evidence",
        &["eval_live_fixture"],
    ),
    (
        "optimizer.status",
        "Read selected optimizer job",
        &["project_endpoint", "optimizer_job_id"],
    ),
    (
        "optimizer.candidate",
        "Read selected optimizer candidate",
        &[
            "project_endpoint",
            "optimizer_job_id",
            "optimizer_candidate_id",
        ],
    ),
    (
        "optimizer.run",
        "Opt-in bounded optimizer submission and polling",
        &[
            "project_endpoint",
            "optimizer_request",
            "allow_optimizer_submit",
        ],
    ),
    (
        "finetune.check",
        "Offline fine-tuning input validation only",
        &["finetune_fixture"],
    ),
    (
        "finetune.consume",
        "Invoke an already deployed fine-tuned model",
        &["finetuned_model_fixture"],
    ),
    (
        "deploy.check",
        "Validate generated deployment artifacts",
        &["deploy_fixture"],
    ),
    (
        "deploy.health",
        "Read deployed agent health",
        &["deployment_health_fixture"],
    ),
];

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaObserveSuiteRuntime;

#[async_trait::async_trait]
impl ObserveSuiteRuntime for CastiaObserveSuiteRuntime {
    fn feature_catalog(&self) -> Value {
        feature_catalog()
    }

    fn validate_suite(&self, suite: &Value) -> Value {
        validate_suite(suite).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn compare_reports(&self, current: &Value, baseline: &Value) -> Value {
        compare_reports(current, baseline).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn run_suite(
        &self,
        suite: &Value,
        probes: &Value,
        prerequisites: &Value,
        baseline: &Value,
        generated_at: &String,
    ) -> Value {
        run_suite(
            suite,
            probes,
            prerequisites,
            if baseline.is_null() {
                None
            } else {
                Some(baseline)
            },
            generated_at,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct ObserveSuiteError(String);

impl std::fmt::Display for ObserveSuiteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ObserveSuiteError {}

pub fn feature_catalog() -> Value {
    json!({
        "schema_version": 1,
        "features": FEATURE_CATALOG.iter().map(|(id, description, prerequisites)| {
            json!({
                "id": id,
                "description": description,
                "prerequisites": prerequisites,
            })
        }).collect::<Vec<_>>()
    })
}

pub fn validate_suite(suite: &Value) -> Result<Value, ObserveSuiteError> {
    let suite = suite.as_object().ok_or_else(|| {
        ObserveSuiteError("suite must contain only name, features, and schema_version".to_string())
    })?;
    if suite
        .keys()
        .any(|key| !matches!(key.as_str(), "name" | "features" | "schema_version"))
    {
        return Err(ObserveSuiteError(
            "suite must contain only name, features, and schema_version".to_string(),
        ));
    }
    if suite.get("schema_version").is_some_and(|value| value != 1) {
        return Err(ObserveSuiteError(
            "suite schema_version must be 1".to_string(),
        ));
    }
    let name = suite
        .get("name")
        .and_then(Value::as_str)
        .filter(|name| !name.trim().is_empty())
        .ok_or_else(|| ObserveSuiteError("suite name is required".to_string()))?;
    let features = suite
        .get("features")
        .and_then(Value::as_array)
        .filter(|features| !features.is_empty())
        .ok_or_else(|| {
            ObserveSuiteError("suite features must be a nonempty list of feature IDs".to_string())
        })?;
    let known = FEATURE_CATALOG
        .iter()
        .map(|(id, _, _)| *id)
        .collect::<BTreeSet<_>>();
    let mut seen = BTreeSet::new();
    let mut normalized = Vec::new();
    for feature in features {
        let feature = feature
            .as_str()
            .ok_or_else(|| ObserveSuiteError("suite contains an unknown feature ID".to_string()))?;
        if !known.contains(feature) {
            return Err(ObserveSuiteError(
                "suite contains an unknown feature ID".to_string(),
            ));
        }
        if !seen.insert(feature) {
            return Err(ObserveSuiteError(
                "suite feature IDs must be unique".to_string(),
            ));
        }
        normalized.push(Value::String(feature.to_string()));
    }
    Ok(json!({"schema_version": 1, "name": name, "features": normalized}))
}

pub fn compare_reports(current: &Value, baseline: &Value) -> Result<Value, ObserveSuiteError> {
    let before = outcomes(baseline)?;
    let after = outcomes(current)?;
    let keys = before
        .keys()
        .chain(after.keys())
        .copied()
        .collect::<BTreeSet<_>>();
    let changed = keys
        .iter()
        .filter(|key| before.get(**key) != after.get(**key))
        .map(|key| json!({"feature_id": key, "before": before.get(*key), "after": after.get(*key)}))
        .collect::<Vec<_>>();
    Ok(json!({
        "changed": changed,
        "regressions": before
            .iter()
            .filter(|(_, status)| **status == "pass")
            .filter(|(key, _)| after.get(*key).copied() != Some("pass"))
            .map(|(key, _)| *key)
            .collect::<Vec<_>>(),
        "lost_coverage": before
            .keys()
            .filter(|key| !after.contains_key(*key))
            .copied()
            .collect::<Vec<_>>(),
        "new_coverage": after
            .keys()
            .filter(|key| !before.contains_key(*key))
            .copied()
            .collect::<Vec<_>>(),
    }))
}

pub fn run_suite(
    suite: &Value,
    probes: &Value,
    prerequisites: &Value,
    baseline: Option<&Value>,
    generated_at: &str,
) -> Result<Value, ObserveSuiteError> {
    let suite = validate_suite(suite)?;
    if let Some(baseline) = baseline {
        compare_reports(&json!({"schema_version": 1, "results": []}), baseline)?;
    }
    let features = suite["features"]
        .as_array()
        .expect("validated suite features");
    let probes = object_or_empty(probes, "probes")?;
    let prerequisites = object_or_empty(prerequisites, "prerequisites")?;
    let generated_at = normalize_generated_at(generated_at)?;
    let selected = features
        .iter()
        .filter_map(Value::as_str)
        .collect::<BTreeSet<_>>();
    let mut results = Vec::new();
    for (feature_id, _, required) in FEATURE_CATALOG {
        if !selected.contains(feature_id) {
            results.push(feature_result(
                feature_id,
                false,
                "uncovered",
                "Not selected.",
                None,
                json!({}),
                None,
            ));
            continue;
        }
        let missing = required
            .iter()
            .filter(|key| !truthy(prerequisites.get(**key)))
            .copied()
            .collect::<Vec<_>>();
        if !missing.is_empty() {
            results.push(feature_result(
                feature_id,
                true,
                "blocked",
                &format!("Missing prerequisites: {}", missing.join(", ")),
                Some("prerequisite"),
                json!({}),
                None,
            ));
            continue;
        }
        let Some(probe) = probes.get(feature_id) else {
            results.push(feature_result(
                feature_id,
                true,
                "uncovered",
                "No probe registered.",
                Some("coverage"),
                json!({}),
                None,
            ));
            continue;
        };
        results.push(run_probe(feature_id, probe));
    }
    let mut report = report(
        suite["name"].as_str().expect("validated suite name"),
        &generated_at,
        results,
        None,
    )?;
    if let Some(baseline) = baseline {
        let comparison = compare_reports(&report, baseline)?;
        report["comparison"] = comparison;
        report["passed"] = Value::Bool(passed(&report));
    }
    Ok(report)
}

fn run_probe(feature_id: &str, probe: &Value) -> Value {
    let Some(object) = probe.as_object() else {
        return feature_result(
            feature_id,
            true,
            "fail",
            INVALID_RESPONSE_DIAGNOSTIC,
            Some("invalid_response"),
            json!({}),
            Some(0.0),
        );
    };
    if let Some(error) = object.get("raises").and_then(Value::as_object) {
        let category = error
            .get("category")
            .and_then(Value::as_str)
            .unwrap_or("unexpected");
        let diagnostic = error
            .get("diagnostic")
            .and_then(Value::as_str)
            .unwrap_or(UNEXPECTED_DIAGNOSTIC);
        let status = if BLOCKING_CATEGORIES.contains(&category) {
            "blocked"
        } else {
            "fail"
        };
        return feature_result(
            feature_id,
            true,
            status,
            diagnostic,
            Some(category),
            json!({}),
            Some(0.0),
        );
    }
    let status = object.get("status").and_then(Value::as_str);
    if !status.is_some_and(|status| STATUSES.contains(&status)) {
        return feature_result(
            feature_id,
            true,
            "fail",
            INVALID_RESPONSE_DIAGNOSTIC,
            Some("invalid_response"),
            json!({}),
            Some(0.0),
        );
    }
    let evidence = object.get("evidence").cloned().unwrap_or_else(|| json!({}));
    if !evidence.is_object() {
        return feature_result(
            feature_id,
            true,
            "fail",
            INVALID_RESPONSE_DIAGNOSTIC,
            Some("invalid_response"),
            json!({}),
            Some(0.0),
        );
    }
    feature_result(
        feature_id,
        true,
        status.unwrap(),
        object
            .get("diagnostic")
            .and_then(Value::as_str)
            .unwrap_or(""),
        object.get("category").and_then(Value::as_str),
        evidence,
        Some(0.0),
    )
}

fn report(
    name: &str,
    generated_at: &str,
    mut results: Vec<Value>,
    comparison: Option<Value>,
) -> Result<Value, ObserveSuiteError> {
    results.sort_by(|a, b| {
        a["feature_id"]
            .as_str()
            .unwrap_or_default()
            .cmp(b["feature_id"].as_str().unwrap_or_default())
    });
    let selected = results
        .iter()
        .filter(|row| row["selected"].as_bool() == Some(true))
        .collect::<Vec<_>>();
    let counts = STATUSES
        .into_iter()
        .map(|status| {
            (
                status.to_string(),
                json!(selected
                    .iter()
                    .filter(|row| row["status"] == status)
                    .count()),
            )
        })
        .collect::<Map<_, _>>();
    let mut report = json!({
        "schema_version": 1,
        "name": name,
        "generated_at": generated_at,
        "passed": false,
        "counts": counts,
        "results": results,
        "comparison": comparison.unwrap_or(Value::Null),
        "cost": null,
        "cost_status": "unknown",
    });
    report["passed"] = Value::Bool(passed(&report));
    Ok(report)
}

fn passed(report: &Value) -> bool {
    let empty = Vec::new();
    let selected = report["results"]
        .as_array()
        .unwrap_or(&empty)
        .iter()
        .filter(|row| row["selected"].as_bool() == Some(true))
        .collect::<Vec<_>>();
    let drift = report
        .get("comparison")
        .and_then(Value::as_object)
        .is_some_and(|comparison| {
            comparison
                .get("regressions")
                .and_then(Value::as_array)
                .is_some_and(|v| !v.is_empty())
                || comparison
                    .get("lost_coverage")
                    .and_then(Value::as_array)
                    .is_some_and(|v| !v.is_empty())
        });
    !selected.is_empty()
        && selected
            .iter()
            .all(|row| row.get("status").and_then(Value::as_str) == Some("pass"))
        && !drift
}

fn outcomes(report: &Value) -> Result<BTreeMap<&str, &str>, ObserveSuiteError> {
    let data = report.as_object().ok_or_else(|| {
        ObserveSuiteError("baseline must be a schema_version 1 report".to_string())
    })?;
    if data.get("schema_version") != Some(&json!(1)) {
        return Err(ObserveSuiteError(
            "baseline must be a schema_version 1 report".to_string(),
        ));
    }
    let results = data
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| ObserveSuiteError("baseline results must be a list".to_string()))?;
    let mut outcomes = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for row in results {
        let row = row.as_object().ok_or_else(|| {
            ObserveSuiteError("baseline contains an invalid feature result".to_string())
        })?;
        let feature_id = row
            .get("feature_id")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                ObserveSuiteError("baseline contains an invalid feature result".to_string())
            })?;
        let status = row.get("status").and_then(Value::as_str).ok_or_else(|| {
            ObserveSuiteError("baseline contains an invalid feature result".to_string())
        })?;
        if !STATUSES.contains(&status) || row.get("selected").and_then(Value::as_bool).is_none() {
            return Err(ObserveSuiteError(
                "baseline contains an invalid feature result".to_string(),
            ));
        }
        if !seen.insert(feature_id) {
            return Err(ObserveSuiteError(
                "baseline contains duplicate feature results".to_string(),
            ));
        }
        if row["selected"] == true {
            outcomes.insert(feature_id, status);
        }
    }
    Ok(outcomes)
}

fn feature_result(
    feature_id: &str,
    selected: bool,
    status: &str,
    diagnostic: &str,
    category: Option<&str>,
    evidence: Value,
    duration_seconds: Option<f64>,
) -> Value {
    json!({
        "feature_id": feature_id,
        "selected": selected,
        "status": status,
        "diagnostic": diagnostic,
        "category": category,
        "evidence": evidence,
        "duration_seconds": duration_seconds,
    })
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Null) | None => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn object_or_empty(value: &Value, name: &str) -> Result<Map<String, Value>, ObserveSuiteError> {
    match value {
        Value::Null => Ok(Map::new()),
        Value::Object(object) => Ok(object.clone()),
        _ => Err(ObserveSuiteError(format!("{name} must be a mapping"))),
    }
}

fn normalize_generated_at(value: &str) -> Result<String, ObserveSuiteError> {
    let parsed = DateTime::parse_from_rfc3339(value).map_err(|_| {
        ObserveSuiteError("report clock must return a timezone-aware datetime".to_string())
    })?;
    Ok(parsed.with_timezone(&Utc).to_rfc3339_opts(
        if parsed.timestamp_subsec_nanos() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        true,
    ))
}
