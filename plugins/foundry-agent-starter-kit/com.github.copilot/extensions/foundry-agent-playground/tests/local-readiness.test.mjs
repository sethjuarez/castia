import assert from "node:assert/strict";
import test from "node:test";
import { localStartupStillPending } from "../state/local-readiness.mjs";

test("localStartupStillPending suppresses failed probes before the startup deadline", () => {
    const state = {
        target: "local",
        localRun: {
            running: true,
            readiness: {
                status: "starting",
                deadlineAt: new Date(Date.now() + 5000).toISOString(),
            },
        },
    };

    assert.equal(localStartupStillPending(state, { ok: false, status: 0, body: "fetch failed" }), true);
});

test("localStartupStillPending stops suppressing failures after startup settles", () => {
    assert.equal(
        localStartupStillPending(
            {
                target: "local",
                localRun: {
                    running: true,
                    readiness: {
                        status: "starting",
                        deadlineAt: new Date(Date.now() - 5000).toISOString(),
                    },
                },
            },
            { ok: false, status: 0, body: "fetch failed" },
        ),
        false,
    );
    assert.equal(
        localStartupStillPending(
            {
                target: "local",
                localRun: {
                    running: true,
                    readiness: {
                        status: "ready",
                        deadlineAt: new Date(Date.now() + 5000).toISOString(),
                    },
                },
            },
            { ok: false, status: 0, body: "fetch failed" },
        ),
        false,
    );
    assert.equal(
        localStartupStillPending(
            {
                target: "local",
                localRun: {
                    running: true,
                    readiness: {
                        status: "starting",
                        deadlineAt: new Date(Date.now() + 5000).toISOString(),
                    },
                },
            },
            { ok: true, status: 200 },
        ),
        false,
    );
});
