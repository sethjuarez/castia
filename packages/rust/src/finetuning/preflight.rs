//! Pure pre-submit checks for fine-tuning jobs, mirroring
//! `castia.finetuning.preflight` and `spec/conformance/finetuning/preflight.json`.

use super::training::FinetuningError;
use serde_json::{json, Value};

pub const DEFAULT_TIER: &str = "developer";
pub const DEFAULT_SECONDS_PER_STEP: f64 = 60.0;
pub const BATCH_SIZE_RANGE: (i64, i64) = (1, 256);
pub const APPROVAL_STAGES: [&str; 3] = ["train", "evaluate", "promote"];
pub const MIN_RFT_TRAINING_ROWS: usize = 10;

/// `(module, evidence, fix)` for Python grader imports the RFT grader service
/// rejects. Mirrors `spec/conformance/finetuning/python_grader_rules.json`.
pub const DENIED_GRADER_MODULES: &[(&str, &str, &str)] = &[(
    "urllib.parse",
    "Waypoint MAI-2 RFT probe ftjob-82ff966f, 2026-11-25: graders validate/run HTTP 400 invalid_grader \"Invalid grader source: use of module 'urllib.parse' is not allowed in Python graders\"",
    "parse the string with str methods or re instead of urllib.parse",
)];

/// The rate table used for the spend line.
pub fn rft_cost_table() -> Value {
    json!({
        "developer": {
            "training_hourly_usd": 40.0,
            "deployment_daily_usd": 8.64,
            "source": "Foundry field notes (developer tier), 2026-11",
        }
    })
}

pub fn cost_rates(tier: &str) -> Result<Value, FinetuningError> {
    rft_cost_table()
        .get(tier)
        .cloned()
        .ok_or_else(|| FinetuningError(format!("unknown cost tier '{tier}' (known: developer)")))
}

fn batch_error(value: &Value) -> FinetuningError {
    FinetuningError(format!(
        "batch_size must be an integer in [{}, {}], or -1/auto to let the service choose; got {}",
        BATCH_SIZE_RANGE.0, BATCH_SIZE_RANGE.1, value
    ))
}

/// An explicit batch size, or `None` for automatic (`-1`, `"auto"`, empty).
pub fn normalize_batch_size(value: &Value) -> Result<Option<i64>, FinetuningError> {
    let number = match value {
        Value::Null => return Ok(None),
        Value::Number(n) => n.as_i64().ok_or_else(|| batch_error(value))?,
        Value::String(s) => {
            let text = s.trim().to_lowercase();
            if text.is_empty() || text == "auto" {
                return Ok(None);
            }
            text.parse::<i64>().map_err(|_| batch_error(value))?
        }
        _ => return Err(batch_error(value)),
    };
    if number == -1 {
        return Ok(None);
    }
    if number < BATCH_SIZE_RANGE.0 || number > BATCH_SIZE_RANGE.1 {
        return Err(batch_error(value));
    }
    Ok(Some(number))
}

fn round_to(value: f64, places: i32) -> f64 {
    let factor = 10f64.powi(places);
    (value * factor).round() / factor
}

fn fmt_g(value: f64) -> String {
    if value.fract() == 0.0 {
        format!("{}", value as i64)
    } else {
        format!("{value}")
    }
}

/// Steps, hours, and cost for a training run; see the Python docstring.
pub fn estimate_training(
    rows: i64,
    n_epochs: Option<i64>,
    batch_size: Option<&Value>,
    seconds_per_step: Option<f64>,
    hourly_usd: Option<f64>,
    tier: &str,
) -> Result<Value, FinetuningError> {
    if rows < 1 {
        return Err(FinetuningError("rows must be a positive integer".into()));
    }
    let mut assumptions: Vec<String> = Vec::new();
    let epochs = match n_epochs {
        None => {
            assumptions.push("n_epochs=1 (service default not known offline)".into());
            1
        }
        Some(e) if e >= 1 => e,
        Some(_) => {
            return Err(FinetuningError(
                "n_epochs must be a positive integer".into(),
            ))
        }
    };
    let explicit = match batch_size {
        None => None,
        Some(value) => normalize_batch_size(value)?,
    };
    let (batch, bound) = match explicit {
        Some(b) => (b, "estimate"),
        None => {
            assumptions.push("batch_size=1 upper bound (batch size is automatic)".into());
            (1, "upper")
        }
    };
    let pace = match seconds_per_step {
        Some(p) => p,
        None => {
            assumptions.push(format!(
                "{}s per step (measure with a probe)",
                fmt_g(DEFAULT_SECONDS_PER_STEP)
            ));
            DEFAULT_SECONDS_PER_STEP
        }
    };
    if !pace.is_finite() || pace <= 0.0 {
        return Err(FinetuningError(
            "seconds_per_step must be finite and positive".into(),
        ));
    }
    let rate = match hourly_usd {
        Some(r) => r,
        None => {
            let r = cost_rates(tier)?["training_hourly_usd"]
                .as_f64()
                .unwrap_or(0.0);
            assumptions.push(format!("${}/h {tier} tier", fmt_g(r)));
            r
        }
    };
    if !rate.is_finite() || rate < 0.0 {
        return Err(FinetuningError(
            "hourly_usd must be finite and non-negative".into(),
        ));
    }
    let steps = ((rows + batch - 1) / batch) * epochs;
    let hours = steps as f64 * pace / 3600.0;
    Ok(json!({
        "rows": rows,
        "n_epochs": epochs,
        "batch_size": batch,
        "steps": steps,
        "hours": round_to(hours, 3),
        "cost_usd": round_to(hours * rate, 2),
        "bound": bound,
        "assumptions": assumptions,
    }))
}

/// Earliest event time showing training running (metrics, "training started", "step ").
pub fn training_started_at(events: &[Value]) -> Option<i64> {
    events
        .iter()
        .filter_map(|event| {
            let created = event.get("created_at").and_then(Value::as_i64)?;
            let message = event
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_lowercase();
            let running = event.get("type").and_then(Value::as_str) == Some("metrics")
                || message.contains("training started")
                || message.contains("started training")
                || message.starts_with("step ");
            running.then_some(created)
        })
        .min()
}

/// Split elapsed time into queue and training seconds.
pub fn split_job_timing(
    created_at: i64,
    started_at: Option<i64>,
    finished_at: Option<i64>,
    now: i64,
) -> Value {
    let end = finished_at.unwrap_or(now);
    let finished = finished_at.is_some();
    match started_at {
        None => json!({
            "phase": if finished { "finished" } else { "queued" },
            "queue_seconds": (end - created_at).max(0),
            "training_seconds": 0,
        }),
        Some(started) => json!({
            "phase": if finished { "finished" } else { "training" },
            "queue_seconds": (started - created_at).max(0),
            "training_seconds": (end - started).max(0),
        }),
    }
}

/// Whether a job delete may proceed; deleting a job deletes its trained model.
pub fn delete_guard(
    fine_tuned_model: Option<&str>,
    deployments: &[Value],
    confirm_model: Option<&str>,
    yes: bool,
) -> Value {
    let Some(model) = fine_tuned_model.filter(|m| !m.is_empty()) else {
        return if yes {
            json!({"allowed": true, "deployments": [], "reason": "job has no trained model"})
        } else {
            json!({"allowed": false, "deployments": [], "reason": "pass --yes to delete this job"})
        };
    };
    let mut serving: Vec<String> = deployments
        .iter()
        .filter(|d| d.get("model").and_then(Value::as_str) == Some(model))
        .map(|d| match d.get("name") {
            Some(Value::String(s)) => s.clone(),
            Some(other) => other.to_string(),
            None => "None".into(),
        })
        .collect();
    serving.sort();
    if confirm_model == Some(model) {
        return json!({"allowed": true, "deployments": serving, "reason": "model delete confirmed"});
    }
    let mut reason = format!("deleting this job deletes model '{model}'");
    if !serving.is_empty() {
        reason.push_str(&format!(
            ", which deployment(s) {} serve",
            serving.join(", ")
        ));
    }
    reason.push_str(&format!("; pass --confirm-model {model} to proceed"));
    json!({"allowed": false, "deployments": serving, "reason": reason})
}

/// The approval record for a trained model; each stage stands alone.
pub fn approvals(train: bool, evaluate: bool, promote: bool) -> Value {
    let state = |granted: bool| if granted { "approved" } else { "required" };
    json!({"train": state(train), "evaluate": state(evaluate), "promote": state(promote)})
}

fn first_name(alias: &str) -> Option<&str> {
    alias
        .split_whitespace()
        .next()
        .filter(|name| !name.is_empty())
}

/// Modules a Python grader imports. A line scanner, not a parser: it reads
/// `import a, b as c` and `from a import b, c` statements (parenthesised
/// continuation lines included), and reports `a.b` for `from a import b`.
pub fn python_grader_imports(source: &str) -> Vec<String> {
    let mut modules = Vec::new();
    let mut pending_from: Option<String> = None;
    for raw in source.lines() {
        let line = raw.split('#').next().unwrap_or("");
        if let Some(module) = pending_from.clone() {
            let body = line.replace(['(', ')'], "");
            for alias in body.split(',') {
                if let Some(name) = first_name(alias) {
                    modules.push(format!("{module}.{name}"));
                }
            }
            if line.contains(')') {
                pending_from = None;
            }
            continue;
        }
        for statement in line.split(';') {
            let statement = statement.trim();
            if let Some(rest) = statement.strip_prefix("import ") {
                for alias in rest.split(',') {
                    if let Some(name) = first_name(alias) {
                        modules.push(name.to_string());
                    }
                }
            } else if let Some(rest) = statement.strip_prefix("from ") {
                let Some((module, names)) = rest.split_once(" import ") else {
                    continue;
                };
                let module = module.trim();
                if module.is_empty() || module.starts_with('.') {
                    continue;
                }
                modules.push(module.to_string());
                if names.contains('(') && !names.contains(')') {
                    pending_from = Some(module.to_string());
                }
                for alias in names.replace(['(', ')'], "").split(',') {
                    if let Some(name) = first_name(alias) {
                        modules.push(format!("{module}.{name}"));
                    }
                }
            }
        }
    }
    modules
}

/// Denied modules (see [`DENIED_GRADER_MODULES`]) a grader imports.
pub fn python_grader_denied_imports(source: &str) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for module in python_grader_imports(source) {
        for (denied, _, _) in DENIED_GRADER_MODULES {
            let hit = module == *denied || module.starts_with(&format!("{denied}."));
            if hit && !found.iter().any(|f| f == denied) {
                found.push((*denied).to_string());
            }
        }
    }
    found
}

/// Fix-it problems for imports the RFT grader service rejects.
pub fn python_grader_problems(source: &str) -> Vec<String> {
    python_grader_denied_imports(source)
        .into_iter()
        .map(|module| {
            let fix = DENIED_GRADER_MODULES
                .iter()
                .find(|(name, _, _)| *name == module)
                .map_or("", |(_, _, fix)| fix);
            format!("python grader imports '{module}', which the RFT grader service rejects; {fix}")
        })
        .collect()
}

/// The service's minimum training rows, as a problem when `rows` is short.
pub fn min_rows_problem(training_type: &str, rows: usize) -> Option<String> {
    (training_type == "rft" && rows < MIN_RFT_TRAINING_ROWS).then(|| {
        format!(
            "training: {rows} example(s); RFT requires at least {MIN_RFT_TRAINING_ROWS} (the service rejects the job with HTTP 400)"
        )
    })
}
