use anyhow::{anyhow, bail, Context, Result};
use castia::inference::{instructions_param, try_reasoning_param};
use castia::protocols::{responses_body, responses_input};
use serde_json::{json, Value};
use std::env;
use std::path::Path;
use std::process::Stdio;
use tokio::process::Command;

const DEFAULT_PROMPT: &str = "Say hello from the Rust minimal agent.";
const AI_FOUNDRY_SCOPE: &str = "https://ai.azure.com/.default";

#[tokio::main]
async fn main() -> Result<()> {
    load_dotenv(".env")?;

    let args = Args::parse(env::args().skip(1))?;
    let prompt = args.prompt.unwrap_or_else(|| DEFAULT_PROMPT.to_string());

    let text = responses_input(&json!([
        {
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        }
    ]));
    if text.is_empty() {
        bail!("Responses input normalization produced empty text");
    }

    if !args.live {
        println!(
            "{}",
            serde_json::to_string_pretty(&responses_body(&format!("offline:{text}")))?,
        );
        return Ok(());
    }

    let config = LiveConfig::from_env()?;
    let output = respond(&config, &text).await?;
    println!(
        "{}",
        serde_json::to_string_pretty(&responses_body(&output))?
    );
    Ok(())
}

#[derive(Debug, Default)]
struct Args {
    live: bool,
    prompt: Option<String>,
}

impl Args {
    fn parse(mut args: impl Iterator<Item = String>) -> Result<Self> {
        let mut parsed = Self::default();
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--offline" => parsed.live = false,
                "--live" => parsed.live = true,
                "--prompt" => {
                    parsed.prompt = Some(args.next().ok_or_else(|| anyhow!("--prompt needs text"))?)
                }
                "-h" | "--help" => {
                    println!(
                        "Usage: cargo run -- [--offline|--live] [--prompt <text>]\n\n\
                         --offline  Run local contract smoke only (default)\n\
                         --live     Call the configured Foundry model deployment"
                    );
                    std::process::exit(0);
                }
                other => bail!("unknown argument: {other}"),
            }
        }
        Ok(parsed)
    }
}

struct LiveConfig {
    project_endpoint: String,
    deployment: String,
    reasoning_effort: Option<String>,
}

impl LiveConfig {
    fn from_env() -> Result<Self> {
        let project_endpoint = required_env("FOUNDRY_PROJECT_ENDPOINT")?
            .trim()
            .trim_end_matches('/')
            .to_string();
        if !looks_like_project_endpoint(&project_endpoint) {
            bail!(
                "FOUNDRY_PROJECT_ENDPOINT must look like https://<account>.services.ai.azure.com/api/projects/<project>"
            );
        }
        let deployment = required_env("AZURE_AI_MODEL_DEPLOYMENT_NAME")?
            .trim()
            .to_string();
        let reasoning_effort = env::var("MODEL_REASONING_EFFORT")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());
        try_reasoning_param(reasoning_effort.as_deref()).map_err(|error| anyhow!(error))?;
        Ok(Self {
            project_endpoint,
            deployment,
            reasoning_effort,
        })
    }
}

async fn respond(config: &LiveConfig, text: &str) -> Result<String> {
    let token = azure_cli_token().await?;
    let url = format!("{}/openai/v1/responses", config.project_endpoint);
    let mut body = json!({
        "model": config.deployment,
        "input": text,
    });
    if let Some(object) = instructions_param(Some(
        "You are the minimal Castia Rust smoke-test agent. Keep replies short.",
    ))
    .as_object()
    {
        merge_object(&mut body, object);
    }
    if let Some(object) = try_reasoning_param(config.reasoning_effort.as_deref())
        .map_err(|error| anyhow!(error))?
        .as_object()
    {
        merge_object(&mut body, object);
    }

    let response = reqwest::Client::new()
        .post(url)
        .bearer_auth(token)
        .json(&body)
        .send()
        .await
        .context("sending Foundry Responses request")?;
    let status = response.status();
    let value: Value = response
        .json()
        .await
        .context("parsing Foundry Responses JSON")?;
    if !status.is_success() {
        bail!("Foundry Responses returned {status}: {value}");
    }
    response_text(&value)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| anyhow!("Foundry response did not include non-empty output text: {value}"))
}

fn response_text(value: &Value) -> Option<String> {
    if let Some(text) = value.get("output_text").and_then(Value::as_str) {
        return Some(text.to_string());
    }
    let output = value.get("output")?.as_array()?;
    let mut text = String::new();
    for item in output {
        let Some(content) = item.get("content").and_then(Value::as_array) else {
            continue;
        };
        for part in content {
            if part.get("type").and_then(Value::as_str) == Some("output_text") {
                if let Some(delta) = part.get("text").and_then(Value::as_str) {
                    text.push_str(delta);
                }
            }
        }
    }
    Some(text)
}

async fn azure_cli_token() -> Result<String> {
    let args = [
        "account",
        "get-access-token",
        "--scope",
        AI_FOUNDRY_SCOPE,
        "--query",
        "accessToken",
        "-o",
        "tsv",
    ];
    let output = match run_az("az", &args).await {
        Ok(output) => output,
        Err(first) if cfg!(windows) => run_az("az.cmd", &args).await.with_context(|| {
            format!("running az account get-access-token; az failed with {first}")
        })?,
        Err(first) => return Err(first).context("running az account get-access-token"),
    };
    if !output.status.success() {
        bail!(
            "az account get-access-token failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    let token = String::from_utf8(output.stdout)
        .context("Azure CLI returned a non-UTF8 token")?
        .trim()
        .to_string();
    if token.is_empty() {
        bail!("Azure CLI returned an empty access token");
    }
    Ok(token)
}

async fn run_az(program: &str, args: &[&str]) -> Result<std::process::Output> {
    Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .await
        .with_context(|| format!("launching {program}"))
}

fn merge_object(body: &mut Value, fields: &serde_json::Map<String, Value>) {
    let object = body.as_object_mut().expect("request body is an object");
    for (key, value) in fields {
        object.insert(key.clone(), value.clone());
    }
}

fn required_env(name: &str) -> Result<String> {
    let value = env::var(name).unwrap_or_default();
    if value.trim().is_empty() || value.trim_start().starts_with('<') {
        bail!("fill {name} in .env or the process environment before running --live");
    }
    Ok(value)
}

fn looks_like_project_endpoint(value: &str) -> bool {
    let Some((host, path)) = value
        .strip_prefix("https://")
        .and_then(|rest| rest.split_once('/'))
    else {
        return false;
    };
    host.contains(".services.ai.azure.com") && path.starts_with("api/projects/") && path.len() > 13
}

fn load_dotenv(path: impl AsRef<Path>) -> Result<()> {
    let path = path.as_ref();
    if !path.exists() {
        return Ok(());
    }
    let content =
        std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if key.is_empty() || env::var_os(key).is_some() {
            continue;
        }
        env::set_var(key, unquote(value.trim()));
    }
    Ok(())
}

fn unquote(value: &str) -> String {
    value
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .or_else(|| {
            value
                .strip_prefix('\'')
                .and_then(|value| value.strip_suffix('\''))
        })
        .unwrap_or(value)
        .to_string()
}
