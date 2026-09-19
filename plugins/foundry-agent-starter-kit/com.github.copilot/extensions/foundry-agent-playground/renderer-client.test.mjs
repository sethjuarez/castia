import assert from "node:assert/strict";
import { test } from "node:test";
import { composerGate, localReadinessState } from "./renderer-client.mjs";

test("composer gate enables local chat when readiness is ok", () => {
    const state = {
        target: "local",
        lastHealth: { ok: true, status: 200 },
        localRun: { running: true },
    };

    assert.deepEqual(composerGate(state), {
        canSend: true,
        inputDisabled: false,
        disabledReason: null,
    });
});

test("composer gate reports precise local readiness states", () => {
    assert.equal(
        composerGate({ target: "local", lastHealth: null, localRun: { running: true } }).disabledReason,
        "Local agent is running; waiting for readiness.",
    );
    assert.equal(
        composerGate({ target: "local", lastHealth: null, localRun: { running: false, exitCode: 1 } }).disabledReason,
        "Local agent stopped. Start local again before chatting.",
    );
    assert.equal(
        composerGate({ target: "local", lastHealth: { ok: false, status: 503, body: "warming" } }).disabledReason,
        "Readiness check failed (503): warming",
    );
});

test("composer gate keeps input enabled while a ready request is in flight", () => {
    const state = {
        target: "local",
        lastHealth: { ok: true, status: 200 },
        localRun: { running: true },
    };

    assert.deepEqual(composerGate(state, { inFlight: true }), {
        canSend: false,
        inputDisabled: false,
        disabledReason: "Waiting for the current response.",
    });
});

test("composer gate blocks hosted chat until a Responses endpoint exists", () => {
    assert.deepEqual(composerGate({ target: "hosted", hosted: {} }), {
        canSend: false,
        inputDisabled: true,
        disabledReason: "Discover or deploy a hosted Responses endpoint before chatting.",
    });
    assert.equal(composerGate({ target: "hosted", hosted: { responsesEndpoint: "https://example.test/responses" } }).canSend, true);
});

test("local readiness ignores prior transcript success for send gating", () => {
    const state = {
        target: "local",
        lastHealth: null,
        localRun: { running: true },
        messages: [{ target: "local", response: { ok: true } }],
    };

    assert.deepEqual(localReadinessState(state), {
        ready: false,
        reason: "Local agent is running; waiting for readiness.",
    });
});

test("local readiness reports stopped agent before stale health can enable send", () => {
    const state = {
        target: "local",
        lastHealth: null,
        localRun: { running: false, exitCode: 1 },
    };

    assert.deepEqual(composerGate(state), {
        canSend: false,
        inputDisabled: true,
        disabledReason: "Local agent stopped. Start local again before chatting.",
    });
});
