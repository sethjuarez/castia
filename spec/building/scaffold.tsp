namespace Castia.Spec.Building;

@doc("Offline project scaffold file map and identifier validation.")
interface BuildScaffoldRuntime {
  @sync
  @vector(#{
    name: "scaffold-files-minimal-agent-project",
    stage: "callable",
    input: #{ name: "test-agent", `model`: "gpt-4o" },
    expected: #{
      appTarget: "main:app",
      provisioned: false,
      fileCount: 15,
      contentDigest: "eb7c0345e7700d61e398db39601f0f2e33c50e7fdc0048f2f0e1327c586d3625",
      files: #[
        ".agent_configs/baseline/instructions.md",
        ".agent_configs/baseline/metadata.yaml",
        ".dockerignore",
        ".env.example",
        ".gitignore",
        "DEPLOYMENT.md",
        "Dockerfile",
        "azure.yaml",
        "eval-seed.jsonl",
        "eval.yaml",
        "main.py",
        "pyproject.toml",
        "requirements-dev.txt",
        "requirements.txt",
        "tests/test_agent.py",
      ],
      snippets: #{
        main: "app = Agent(name='test-agent')",
        pyproject: "name = \"test-agent\"",
        manifest: "runtime: python_3_13",
        metadata: "model: \"gpt-4o\"",
      },
    },
  })
  @vector(#{
    name: "scaffold-files-rejects-invalid-name",
    stage: "callable",
    input: #{ name: "UPPER", `model`: "gpt-4o" },
    expectedError: #{ message: "name must be a lowercase letter followed by letters, digits or '-'." },
  })
  @vector(#{
    name: "scaffold-files-rejects-invalid-model",
    stage: "callable",
    input: #{ name: "test-agent", `model`: "../model" },
    expectedError: #{ message: "model must be a deployment identifier, not a path or expression." },
  })
  scaffoldFiles(name: string, `model`: string): unknown;
}
