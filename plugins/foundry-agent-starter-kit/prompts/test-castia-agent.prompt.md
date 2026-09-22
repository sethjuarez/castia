# Test a Castia agent

Try the Castia agent in this repository and report evidence.

Start local. Run the smallest test or eval that proves the agent handles the main scenario. If a Castia canvas or local chat surface exists, use the open canvas instance after tests pass and record one short transcript. For Foundry Agent Playground, focus the canvas with `open_canvas`, then drive `set_target` when needed, `health_check`, `send_response`, and `get_transcript_state` with `invoke_canvas_action` on the same `instanceId`. Never use Playwright, browser navigation, or direct canvas URL automation for this Playground; those paths bypass the shipped side-panel canvas and do not prove collaboration, layout, transcript, or action visibility.

Do not provision, deploy, publish to Teams, or grant permissions unless I approve that action in this session.

Report:

- The command you ran.
- The result.
- One successful prompt and response.
- One failure or edge case if the repo has data for it.
- The next blocked step, if any.
