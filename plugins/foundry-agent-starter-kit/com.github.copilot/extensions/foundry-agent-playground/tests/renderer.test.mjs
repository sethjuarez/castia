import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { renderHtml } from "../renderer/renderer.mjs";

test("renderer shell loads committed browser asset", () => {
    const html = renderHtml({ pluginVersion: "0.1.97" });

    assert.match(html, /<script src="\/assets\/playground-client\.js"><\/script>/);
    assert.doesNotMatch(html, /<script>\s*const isStartupReadinessPending/);
    assert.match(html, /id="transcript" class="transcript empty-state"/);
    assert.match(html, /activityLog/);
    assert.match(html, /class="activity-title">log</);
    assert.match(html, /class="action-detail-trigger"/);
    assert.match(html, /class="status-row"/);
    assert.match(html, /id="guideTitle" class="action-title"[\s\S]*id="actionStateChip"[\s\S]*class="action-detail-trigger"/);
    assert.match(html, /class="plugin-version"[^>]*>v0\.1\.97<\/div>[\s\S]*<div class="right-actions">\s*<button id="clear" class="icon-button" type="button" aria-label="Clear" title="Clear"><svg aria-hidden="true" viewBox="0 0 24 24">[\s\S]*<button class="primary icon-button" id="send" type="button" aria-label="Send" title="Send"><svg aria-hidden="true" viewBox="0 0 24 24">/);
    assert.match(html, /class="activity-head" aria-label="Toggle operation log"/);
    assert.match(html, /class="activity-icon" aria-hidden="true"><svg viewBox="0 0 24 24">/);
    assert.doesNotMatch(html, /id="provisionButton"/);
    assert.doesNotMatch(html, /id="deployButton"/);
    assert.match(html, /class="gate-logo a365-logo" src="\/assets\/icon-a365-agents\.svg"/);
    assert.doesNotMatch(html, /id="teamsStatus"/);
    assert.doesNotMatch(html, /Publish and hire the hosted agent/);
});

test("built browser asset includes pending and answer-copy chat affordances", async () => {
    const script = await readFile(new URL("../renderer/dist/playground-client.js", import.meta.url), "utf8");

    assert.match(script, /Thinking\.\.\./);
    assert.match(script, /Copy answer/);
    assert.match(script, /copy-answer/);
    assert.match(script, /Expand diagram/);
    assert.match(script, /mermaid-lightbox/);
    assert.match(script, /mermaid-zoom-in/);
    assert.doesNotMatch(script, /copyLatestAnswer/);
    assert.doesNotMatch(script, /transcript-copy-latest/);
});
