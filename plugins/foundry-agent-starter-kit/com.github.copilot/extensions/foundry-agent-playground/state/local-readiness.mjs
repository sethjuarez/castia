export function localStartupStillPending(state, health) {
    if (state.target !== "local" || !state.localRun?.running) return false;
    if (state.localRun.readiness?.status !== "starting") return false;
    if (health?.ok) return false;
    const deadline = Date.parse(state.localRun.readiness?.deadlineAt || "");
    return Number.isFinite(deadline) && Date.now() < deadline;
}
