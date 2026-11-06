# TwichUI Workbench current status

Last updated November 5, 2026 (Mountain time).

- **Running app.** Port 9487 still runs build `fcf74ce`. The isolated demonstration's write API and worker are stopped. Its read-only planning hold is available until 8:52 p.m. MST. Normal model access on port 8080 was verified.
- **Finished results.** The earlier supervised Qwen run produced an independently checked synthetic patch and saved review. An earlier supervised Chronicle run also produced a checked result. A separate guild task reached a draft PR with a Codex-authored patch after local attempts failed.
- **Tests and demonstrations.** Fresh browser requests reached checked, failed and recovered results with simulated model responses and real disposable Lua checks. The real browser request reached Qwen, but planning was cancelled. Zero fresh browser tasks have finished end to end with real model responses.
- **What is stopping progress.** An operator database check cancelled planning before a reviewable proposal. The check was fixed and tested offline. The real addon's test-loading patterns remain a separate gap.
- **Next complete outcome.** Take a fresh synthetic browser request through real planning, owner approval, local implementation, independent checks and saved review.
- **Decision needed.** Fresh authorization is needed for another real-model attempt. Task approval still happens in the browser. Publication remains separate.
