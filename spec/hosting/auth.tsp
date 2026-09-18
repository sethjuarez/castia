namespace Castia.Spec.Hosting;

@doc("Hosted-vs-local run detection. Environment collection stays host-specific.")
interface HostingAuthRuntime {
  @sync
  @vector(#{
    name: "agent-digital-worker-is-local",
    stage: "callable",
    input: #{ env: #{ AGENT_DIGITAL_WORKER: "1", FOUNDRY_AGENT_TENANT_ID: "tenant" } },
    expected: true,
  })
  @vector(#{
    name: "missing-hosted-tenant-is-local",
    stage: "callable",
    input: #{ env: #{} },
    expected: true,
  })
  @vector(#{
    name: "hosted-tenant-without-local-flag-is-hosted",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "tenant" } },
    expected: false,
  })
  localRun(env: unknown): boolean;
}
