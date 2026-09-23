import assert from "node:assert/strict";
import { test } from "node:test";
import {
    createActivityTurn,
    finishActivityTurn,
    latestVisibleActivityText,
} from "../protocols/activity-protocol.mjs";

test("createActivityTurn tracks raw activity correlation fields from the sent payload", () => {
    const state = { target: "local", connectorBaseUrl: "http://127.0.0.1:5000/connector" };
    const turn = createActivityTurn(state, "fallback text", {
        endpoint: "http://127.0.0.1:8088/activity/messages",
        raw: {
            id: "raw-turn",
            serviceUrl: "https://example.invalid/connector",
            text: "raw text",
            conversation: { id: "raw-conversation" },
        },
    });

    assert.equal(turn.id, "raw-turn");
    assert.equal(turn.activity.conversationId, "raw-conversation");
    assert.equal(turn.activity.inboundActivityId, "raw-turn");
    assert.equal(turn.activity.serviceUrl, "http://127.0.0.1:5000/connector");
    assert.equal(turn.request.body.serviceUrl, "http://127.0.0.1:5000/connector");
    assert.equal(turn.request.body.channelId, "msteams");
    assert.equal(turn.request.body.conversation.id, "raw-conversation");
    assert.equal(turn.events[0].activityId, "raw-turn");
});

test("latestVisibleActivityText ignores deleted messages and applies updates by activity id", () => {
    const turn = {
        events: [
            { kind: "outbound.message", activityId: "first", text: "first draft" },
            { kind: "outbound.message", activityId: "temp", text: "temporary" },
            { kind: "outbound.message.update", activityId: "first", text: "first updated" },
            { kind: "outbound.message.delete", activityId: "temp" },
        ],
    };

    assert.equal(latestVisibleActivityText(turn), "first updated");
});

test("finishActivityTurn does not copy deleted Activity messages", () => {
    const turn = {
        target: "local",
        events: [
            { kind: "outbound.message", activityId: "temp", text: "temporary" },
            { kind: "outbound.message.delete", activityId: "temp" },
        ],
    };

    finishActivityTurn(turn, { ok: true, status: 200, durationMs: 1, body: { id: "ack" } });

    assert.deepEqual(turn.response.body, { id: "ack" });
});
