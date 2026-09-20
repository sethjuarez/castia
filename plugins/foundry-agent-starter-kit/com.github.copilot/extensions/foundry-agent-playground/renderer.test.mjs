import assert from "node:assert/strict";
import { test } from "node:test";
import { renderHtml } from "./renderer.mjs";

test("renderer includes pending and answer-copy chat affordances", () => {
    const html = renderHtml();

    assert.match(html, /Thinking\.\.\./);
    assert.match(html, /Copy answer/);
    assert.match(html, /copy-answer/);
    assert.match(html, /copyLatestAnswer/);
    assert.match(html, /transcript-copy-latest/);
});
