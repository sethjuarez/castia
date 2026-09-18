use crate::model::StreamingRuntime;
use serde_json::{json, Map, Value};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaStreamingRuntime;

#[async_trait::async_trait]
impl StreamingRuntime for CastiaStreamingRuntime {
    fn append_plan(
        &self,
        finished: &bool,
        current_text: &String,
        delta: &String,
        last_flush: &f64,
        now: &f64,
        min_interval: &f64,
    ) -> Value {
        append_plan(
            *finished,
            current_text,
            delta,
            *last_flush,
            *now,
            *min_interval,
        )
    }

    fn capture_stream_id(
        &self,
        current_stream_id: &Option<String>,
        created_id: &Option<String>,
    ) -> Option<String> {
        capture_stream_id(current_stream_id.as_deref(), created_id.as_deref())
    }

    fn finish_text(&self, current_text: &String, override_text: &Option<String>) -> String {
        finish_text(current_text, override_text.as_deref())
    }

    fn finish_allowed(&self, finished: &bool) -> bool {
        finish_allowed(*finished)
    }

    fn next_flush(&self, last_flush: &f64, now: &f64, emitted: &bool) -> Value {
        next_flush(*last_flush, *now, *emitted)
    }

    fn next_sequence(&self, sequence: &i32, is_final: &bool) -> i32 {
        next_sequence(*sequence, *is_final)
    }

    fn stream_chunk(
        &self,
        stream_type: &String,
        text: &String,
        stream_id: &Option<String>,
        sequence: &i32,
        is_final: &bool,
        attachments: &Value,
        suggestions: &Value,
    ) -> Value {
        stream_chunk(
            stream_type,
            text,
            stream_id.as_deref(),
            *sequence,
            *is_final,
            attachments,
            suggestions,
        )
    }

    fn update_allowed(&self, finished: &bool, current_text: &String) -> bool {
        update_allowed(*finished, current_text)
    }
}

pub fn stream_chunk(
    stream_type: &str,
    text: &str,
    stream_id: Option<&str>,
    sequence: i32,
    final_: bool,
    attachments: &Value,
    suggestions: &Value,
) -> Value {
    let mut info = Map::new();
    info.insert(
        "streamType".to_string(),
        Value::String(stream_type.to_string()),
    );
    if let Some(stream_id) = stream_id.filter(|value| !value.is_empty()) {
        info.insert("streamId".to_string(), Value::String(stream_id.to_string()));
    }
    if !final_ {
        info.insert("streamSequence".to_string(), json!(sequence + 1));
    }

    let mut payload = Map::new();
    payload.insert(
        "type".to_string(),
        Value::String(if final_ { "message" } else { "typing" }.to_string()),
    );
    payload.insert("channelData".to_string(), Value::Object(info.clone()));
    let mut entity = info;
    entity.insert("type".to_string(), Value::String("streamInfo".to_string()));
    payload.insert("entities".to_string(), json!([Value::Object(entity)]));
    if !text.is_empty() {
        payload.insert("text".to_string(), Value::String(text.to_string()));
    }
    if final_ {
        if is_truthy(attachments) {
            payload.insert("attachments".to_string(), attachments.clone());
        }
        if is_truthy(suggestions) {
            payload.insert("suggestedActions".to_string(), suggestions.clone());
        }
    }
    Value::Object(payload)
}

pub fn capture_stream_id(
    current_stream_id: Option<&str>,
    created_id: Option<&str>,
) -> Option<String> {
    current_stream_id
        .filter(|value| !value.is_empty())
        .or_else(|| created_id.filter(|value| !value.is_empty()))
        .map(str::to_string)
}

pub fn update_allowed(finished: bool, current_text: &str) -> bool {
    !finished && current_text.is_empty()
}

pub fn finish_allowed(finished: bool) -> bool {
    !finished
}

pub fn next_sequence(sequence: i32, final_: bool) -> i32 {
    if final_ {
        sequence
    } else {
        sequence + 1
    }
}

pub fn next_flush(last_flush: f64, now: f64, emitted: bool) -> Value {
    if emitted {
        json_number(now)
    } else {
        json_number(last_flush)
    }
}

pub fn append_plan(
    finished: bool,
    current_text: &str,
    delta: &str,
    last_flush: f64,
    now: f64,
    min_interval: f64,
) -> Value {
    if finished || delta.is_empty() {
        return json!({ "text": current_text, "flush": false, "nextFlush": json_number(last_flush) });
    }
    let text = format!("{current_text}{delta}");
    let flush = (now - last_flush) >= min_interval;
    json!({
        "text": text,
        "flush": flush,
        "nextFlush": next_flush(last_flush, now, flush),
    })
}

pub fn finish_text(current_text: &str, override_text: Option<&str>) -> String {
    override_text.unwrap_or(current_text).to_string()
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
        Value::Number(number) => number.as_f64().is_none_or(|value| value != 0.0),
    }
}

fn json_number(value: f64) -> Value {
    if value.is_finite()
        && value.fract() == 0.0
        && value >= i64::MIN as f64
        && value < i64::MAX as f64
    {
        Value::Number((value as i64).into())
    } else {
        json!(value)
    }
}
