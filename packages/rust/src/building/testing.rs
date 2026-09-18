use crate::model::BuildTestingRuntime;
use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct TestingError(String);

impl std::fmt::Display for TestingError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for TestingError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaBuildTestingRuntime;

#[async_trait::async_trait]
impl BuildTestingRuntime for CastiaBuildTestingRuntime {
    fn project_test_report(
        &self,
        root: &String,
        exit_code: &i32,
        output: &String,
        timed_out: &bool,
    ) -> Value {
        project_test_report(root, *exit_code, output, *timed_out)
    }

    fn validate_test_timeout(&self, timeout: &f64) -> f64 {
        validate_test_timeout(*timeout).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn validate_test_timeout(timeout: f64) -> Result<f64, TestingError> {
    if timeout.is_finite() && timeout > 0.0 {
        Ok(timeout)
    } else {
        Err(TestingError(
            "timeout must be a finite positive number of seconds".to_string(),
        ))
    }
}

pub fn project_test_report(root: &str, exit_code: i32, output: &str, timed_out: bool) -> Value {
    if timed_out {
        return json!({
            "root": root,
            "status": "timeout",
            "exit_code": 124,
            "output": "Local tests exceeded the timeout.",
            "ok": false,
            "scope": "offline",
        });
    }
    let status = match exit_code {
        0 => "pass",
        1 => "fail",
        _ => "error",
    };
    json!({
        "root": root,
        "status": status,
        "exit_code": exit_code,
        "output": output,
        "ok": status == "pass",
        "scope": "offline",
    })
}
