use castia::building::{project_files, scaffold_files};
use serde_json::json;

#[test]
fn scaffold_descriptor_matches_python_project_contract() {
    let descriptor = scaffold_files("test-agent", "gpt-4o").unwrap();
    assert_eq!(descriptor["app_target"], "main:app");
    assert_eq!(descriptor["provisioned"], false);
    assert_eq!(descriptor["file_count"], 15);
    assert_eq!(
        descriptor["content_digest"],
        "eb7c0345e7700d61e398db39601f0f2e33c50e7fdc0048f2f0e1327c586d3625"
    );
    assert!(descriptor["files"]
        .as_array()
        .unwrap()
        .contains(&json!("tests/test_agent.py")));

    let files = project_files("test-agent", "gpt-4o").unwrap();
    assert_eq!(files.len(), 15);
    assert_eq!(
        files[".env.example"].as_str().unwrap(),
        "FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>\nAZURE_AI_MODEL_DEPLOYMENT_NAME=<deployment-name>\n"
    );
    let main = files["main.py"].as_str().unwrap();
    assert!(main.contains("app = Agent(name='test-agent')"));
    assert!(main.contains("@app.activity(Teams.direct)"));
    assert!(main.contains("@app.responses()"));
    assert!(main.contains("@app.invocations()"));
    assert!(main.contains("os.environ.get(\"HOST\", \"0.0.0.0\")"));
    assert!(main.contains("os.environ.get(\"PORT\", \"8088\")"));
    assert!(main.contains(".strip().rstrip(\"/\")"));

    let manifest = files["azure.yaml"].as_str().unwrap();
    assert!(manifest.contains("host: azure.ai.agent"));
    assert!(manifest.contains("kind: hosted"));
    assert!(manifest.contains("runtime: python_3_13"));
    assert!(manifest.contains("protocol: activity"));
    assert!(manifest.contains("protocol: responses"));
    assert!(manifest.contains("protocol: invocations"));
    assert!(manifest.contains("cpu: '0.5'"));

    assert!(files["DEPLOYMENT.md"]
        .as_str()
        .unwrap()
        .contains("azd resolves the tenant from the subscription"));
    assert!(files["Dockerfile"]
        .as_str()
        .unwrap()
        .contains("EXPOSE 8088"));
    assert_eq!(
        files[".agent_configs/baseline/metadata.yaml"]
            .as_str()
            .unwrap(),
        "model: \"gpt-4o\"\ninstruction_file: instructions.md\n"
    );
    assert_eq!(
        files["eval-seed.jsonl"].as_str().unwrap(),
        "{\"query\": \"What is 2 + 2?\", \"ground_truth\": \"4\"}\n"
    );
}

#[test]
fn scaffold_rejects_injected_identifiers() {
    for name in ["../oops", "x\nservices: bad", "UPPER", ""] {
        assert!(scaffold_files(name, "gpt-4o").is_err(), "{name:?}");
    }
    for model in ["../model", "x\nsecret", "';bad", ""] {
        assert!(scaffold_files("test-agent", model).is_err(), "{model:?}");
    }
}
