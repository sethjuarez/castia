use castia::hosting::local_run;
use serde_json::json;

#[test]
fn local_run_matches_python_environment_gate() {
    assert!(local_run(
        &json!({"AGENT_DIGITAL_WORKER": "1", "FOUNDRY_AGENT_TENANT_ID": "tenant"})
    ));
    assert!(local_run(&json!({})));
    assert!(!local_run(&json!({"FOUNDRY_AGENT_TENANT_ID": "tenant"})));
}
