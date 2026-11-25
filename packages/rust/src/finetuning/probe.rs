//! Pure RFT probe rules, mirroring `castia.finetuning.probe` and
//! `spec/conformance/finetuning/probe.json`. Python graders need a Python
//! host, so [`grade_local`] returns `Ok(None)` for them here.

use super::training::FinetuningError;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashMap;

pub const PROBE_ROWS: usize = 10;
pub const PASS_FLOOR: f64 = 0.9;
pub const FAIL_CEILING: f64 = 0.1;
pub const DEFAULT_TOLERANCE: f64 = 0.15;
pub const ALWAYS_TRUE_SOURCE: &str = "def grade(sample, item):\n    return 1.0\n";
pub const ALWAYS_FALSE_SOURCE: &str = "def grade(sample, item):\n    return 0.0\n";

pub fn probe_graders() -> Value {
    json!({
        "always_true": {"type": "python", "name": "probe_always_true", "source": ALWAYS_TRUE_SOURCE},
        "always_false": {"type": "python", "name": "probe_always_false", "source": ALWAYS_FALSE_SOURCE},
    })
}

fn lookup<'a>(root: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.')
        .try_fold(root, |value, part| value.get(part))
}

/// Render `{{ sample.* }}` / `{{ item.* }}`; unknown paths render as `""`.
pub fn render_template(template: &str, sample: &Value, item: &Value) -> String {
    let pattern = Regex::new(r"\{\{\s*(sample|item)\.([A-Za-z0-9_.]+)\s*\}\}").unwrap();
    pattern
        .replace_all(template, |caps: &regex::Captures| {
            let root = if &caps[1] == "sample" { sample } else { item };
            match lookup(root, &caps[2]) {
                None => String::new(),
                Some(Value::String(s)) => s.clone(),
                Some(other) => other.to_string(),
            }
        })
        .into_owned()
}

/// The `sample` namespace for one model output.
pub fn sample_for(output: &Value) -> Value {
    let text = match output {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    let parsed = serde_json::from_str::<Value>(&text).unwrap_or(Value::Null);
    json!({"output_text": text, "output_json": parsed})
}

/// Score one row in-process; `None` for grader types that need a model,
/// endpoint, or Python host.
pub fn grade_local(
    grader: &Value,
    sample: &Value,
    item: &Value,
) -> Result<Option<f64>, FinetuningError> {
    match grader.get("type").and_then(Value::as_str) {
        Some("string_check") => {
            let text = |key: &str| grader.get(key).and_then(Value::as_str).unwrap_or("");
            let left = render_template(text("input"), sample, item);
            let right = render_template(text("reference"), sample, item);
            let hit = match grader
                .get("operation")
                .and_then(Value::as_str)
                .unwrap_or("eq")
            {
                "eq" => left == right,
                "ne" => left != right,
                "like" => left.contains(&right),
                "ilike" => left.to_lowercase().contains(&right.to_lowercase()),
                other => {
                    return Err(FinetuningError(format!(
                        "string_check: unknown operation '{other}'"
                    )))
                }
            };
            Ok(Some(if hit { 1.0 } else { 0.0 }))
        }
        Some("multi") => {
            let mut scores = HashMap::new();
            let empty = Map::new();
            let subs = grader
                .get("graders")
                .and_then(Value::as_object)
                .unwrap_or(&empty);
            for (key, sub) in subs {
                match grade_local(sub, sample, item)? {
                    Some(score) => {
                        scores.insert(key.clone(), score);
                    }
                    None => return Ok(None),
                }
            }
            let expression = grader
                .get("calculate_output")
                .and_then(Value::as_str)
                .unwrap_or("");
            calculate(expression, &scores).map(Some)
        }
        _ => Ok(None),
    }
}

/// Evaluate a `calculate_output` expression: numbers, sub-grader names,
/// `+ - * / **`, parentheses, and `min max abs sqrt log`.
pub fn calculate(expression: &str, scores: &HashMap<String, f64>) -> Result<f64, FinetuningError> {
    let tokens = tokenize(expression)?;
    let mut parser = Parser {
        tokens,
        pos: 0,
        scores,
    };
    let value = parser.sum()?;
    if parser.pos != parser.tokens.len() {
        return Err(parser.error());
    }
    Ok(value)
}

#[derive(Clone, Debug, PartialEq)]
enum Token {
    Num(f64),
    Name(String),
    Op(&'static str),
}

fn tokenize(text: &str) -> Result<Vec<Token>, FinetuningError> {
    let chars: Vec<char> = text.chars().collect();
    let mut tokens = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_whitespace() {
            i += 1;
        } else if c.is_ascii_digit() || c == '.' {
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_digit() || chars[i] == '.') {
                i += 1;
            }
            let literal: String = chars[start..i].iter().collect();
            let number = literal.parse().map_err(|_| {
                FinetuningError(format!("calculate_output: bad number '{literal}'"))
            })?;
            tokens.push(Token::Num(number));
        } else if c.is_alphabetic() || c == '_' {
            let start = i;
            while i < chars.len() && (chars[i].is_alphanumeric() || chars[i] == '_') {
                i += 1;
            }
            tokens.push(Token::Name(chars[start..i].iter().collect()));
        } else {
            let op = match c {
                '*' if chars.get(i + 1) == Some(&'*') => {
                    i += 1;
                    "**"
                }
                '+' => "+",
                '-' => "-",
                '*' => "*",
                '/' => "/",
                '(' => "(",
                ')' => ")",
                ',' => ",",
                _ => {
                    return Err(FinetuningError(format!(
                        "calculate_output: unexpected '{c}'"
                    )))
                }
            };
            tokens.push(Token::Op(op));
            i += 1;
        }
    }
    Ok(tokens)
}

struct Parser<'a> {
    tokens: Vec<Token>,
    pos: usize,
    scores: &'a HashMap<String, f64>,
}

impl Parser<'_> {
    fn error(&self) -> FinetuningError {
        FinetuningError(format!(
            "calculate_output: unsupported expression near token {}",
            self.pos
        ))
    }

    fn eat(&mut self, op: &str) -> bool {
        if matches!(self.tokens.get(self.pos), Some(Token::Op(o)) if *o == op) {
            self.pos += 1;
            true
        } else {
            false
        }
    }

    fn sum(&mut self) -> Result<f64, FinetuningError> {
        let mut value = self.product()?;
        loop {
            if self.eat("+") {
                value += self.product()?;
            } else if self.eat("-") {
                value -= self.product()?;
            } else {
                return Ok(value);
            }
        }
    }

    fn product(&mut self) -> Result<f64, FinetuningError> {
        let mut value = self.unary()?;
        loop {
            if self.eat("*") {
                value *= self.unary()?;
            } else if self.eat("/") {
                value /= self.unary()?;
            } else {
                return Ok(value);
            }
        }
    }

    fn unary(&mut self) -> Result<f64, FinetuningError> {
        if self.eat("-") {
            return Ok(-self.unary()?);
        }
        if self.eat("+") {
            return self.unary();
        }
        self.power()
    }

    fn power(&mut self) -> Result<f64, FinetuningError> {
        let base = self.atom()?;
        if self.eat("**") {
            return Ok(base.powf(self.unary()?));
        }
        Ok(base)
    }

    fn atom(&mut self) -> Result<f64, FinetuningError> {
        match self.tokens.get(self.pos).cloned() {
            Some(Token::Num(n)) => {
                self.pos += 1;
                Ok(n)
            }
            Some(Token::Name(name)) => {
                self.pos += 1;
                if self.eat("(") {
                    let mut args = vec![self.sum()?];
                    while self.eat(",") {
                        args.push(self.sum()?);
                    }
                    if !self.eat(")") {
                        return Err(self.error());
                    }
                    return apply(&name, &args).ok_or_else(|| self.error());
                }
                self.scores.get(&name).copied().ok_or_else(|| self.error())
            }
            Some(Token::Op("(")) => {
                self.pos += 1;
                let value = self.sum()?;
                if !self.eat(")") {
                    return Err(self.error());
                }
                Ok(value)
            }
            _ => Err(self.error()),
        }
    }
}

fn apply(name: &str, args: &[f64]) -> Option<f64> {
    match (name, args) {
        ("min", [_, ..]) => args.iter().copied().reduce(f64::min),
        ("max", [_, ..]) => args.iter().copied().reduce(f64::max),
        ("abs", [x]) => Some(x.abs()),
        ("sqrt", [x]) => Some(x.sqrt()),
        ("log", [x]) => Some(x.ln()),
        _ => None,
    }
}

/// Mean of every numeric `*reward*` value in `metrics` events (provisional key match).
pub fn reward_from_events(events: &[Value]) -> Option<f64> {
    let values: Vec<f64> = events
        .iter()
        .filter(|e| e.get("type").and_then(Value::as_str) == Some("metrics"))
        .filter_map(|e| e.get("data").and_then(Value::as_object))
        .flat_map(|data| {
            data.iter()
                .filter(|(key, _)| key.to_lowercase().contains("reward"))
                .filter_map(|(_, value)| value.as_f64())
                .filter(|value| value.is_finite())
        })
        .collect();
    if values.is_empty() {
        return None;
    }
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    Some((mean * 1e6).round() / 1e6)
}

/// `pass`, `fail` (blocks a full submit), or `pending`, with reasons.
pub fn probe_verdict(rewards: &Value, offline_score: Option<f64>, tolerance: f64) -> Value {
    let mut reasons = Vec::new();
    let mut pending = Vec::new();
    match rewards.get("always_true").and_then(Value::as_f64) {
        None => pending.push("always_true reward not observed yet".to_string()),
        Some(r) if r < PASS_FLOOR => reasons.push(format!(
            "always_true reward {r} < {PASS_FLOOR}: the trainer is not reading grader output as reward"
        )),
        Some(_) => {}
    }
    match rewards.get("always_false").and_then(Value::as_f64) {
        None => pending.push("always_false reward not observed yet".to_string()),
        Some(r) if r > FAIL_CEILING => reasons.push(format!(
            "always_false reward {r} > {FAIL_CEILING}: reward does not track the grader"
        )),
        Some(_) => {}
    }
    if let Some(real) = rewards.get("real") {
        match (real.as_f64(), offline_score) {
            (None, _) => pending.push("real grader reward not observed yet".to_string()),
            (Some(_), None) => {
                pending.push("no offline score; run finetune grade-local".to_string())
            }
            (Some(r), Some(o)) if (r - o).abs() > tolerance => reasons.push(format!(
                "trainer reward {r} diverges from offline score {o} by more than {tolerance}: check grader templates and item.* fields"
            )),
            _ => {}
        }
    }
    let status = if !reasons.is_empty() {
        "fail"
    } else if !pending.is_empty() {
        "pending"
    } else {
        "pass"
    };
    json!({"status": status, "reasons": reasons, "pending": pending})
}
