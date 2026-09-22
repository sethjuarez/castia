import assert from "node:assert/strict";
import { test } from "node:test";
import { renderHtml } from "../renderer/renderer.mjs";

test("renderer includes pending and answer-copy chat affordances", () => {
    const html = renderHtml();

    assert.match(html, /Thinking\.\.\./);
    assert.match(html, /Copy answer/);
    assert.match(html, /copy-answer/);
    assert.match(html, /copyLatestAnswer/);
    assert.match(html, /transcript-copy-latest/);
    assert.match(html, /activityLog/);
    assert.match(html, /class="activity-title">Activity</);
    assert.match(html, /id="transcript" class="transcript empty-state"/);
    assert.match(html, /class="action-detail-trigger"/);
    assert.match(html, /class="status-row"/);
    assert.match(html, /id="guideTitle" class="action-title"[\s\S]*id="actionStateChip"[\s\S]*class="action-detail-trigger"/);
    assert.match(html, /class="gate-logo a365-logo" src="\/assets\/icon-a365-agents\.svg"/);
    assert.doesNotMatch(html, /id="teamsStatus"/);
    assert.doesNotMatch(html, /Publish and hire the hosted agent/);
});
