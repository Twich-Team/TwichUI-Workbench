# Using the installed Workbench

Current demonstration and acceptance status: [CURRENT_STATUS](CURRENT_STATUS.md).
This guide describes the supported workflow, not completion of the full product.

The independently accepted flight-path repair is the saved result at
`/executions/143ecdc6-e05d-5997-9c45-aafb9a1a88a2` (candidate `e2f1341…`).
It preserves per-character learned paths across a fresh saved-data load in the
offline client. It has not been installed into the live game addon.
An older attempt, execution `2efed208-b38a-5d09-bd85-4fe68ea9b702`, passed its
then-current native checks but was subsequently rejected by the independent
API-shaped review. Its historical native status is preserved; do not use that
older patch. Its historical result and run listing now show the later rejection;
the original native check outcomes and usage remain recorded. A rejected or
unavailable later review holds publication and grants no new run permission.

Open `http://127.0.0.1:9487` on the server, or `https://10.0.0.9:9487` on the
trusted home LAN. Each browser device must trust the existing Workbench server CA
for HTTPS. This is server identification, not a client certificate or dashboard
password. Any device on the trusted subnet has operator access.

## Everyday navigation

The styled interface uses Dashboard, Backlog, Results and Settings. Dashboard
shows actual work, questions/approvals and recent outcomes. Search and status
filters in Backlog retain completed and failed requests. Results leads to saved
patches and checks. Older sample/imported records remain in advanced history.
Settings links model configuration; opening it does not change saved choices.
Features, inspections, simulator and data work can be saved for later; their
labels explain that no model or execution starts. Check CURRENT_STATUS for the
actual installed candidate; this guide alone is not installation evidence.

## Report the next supported bug

1. Choose **Create a task**. **Fix an addon issue** is the default work type.
2. Describe what happens, what you expected, and what triggers it. Include a Lua
   error if there is one. You do not need to name files or register a new task type.
3. Choose the provider policy and review its consent. Local Qwen keeps the source
   local. Codex additionally requires applicable external-source permission and
   included-allowance eligibility; choosing it does not expand disclosure rights.
4. Answer any actual behavior question. Review the proposed repair scope and
   behavior criteria before approving the plan.
5. Review the separate run offer and limits. Approval permits an attempt in an
   isolated copy; it does not mean the repair is already correct.
6. Workbench can develop code and tests together and revise from check feedback.
   A checked result requires the independent checks, including regression against
   the original code. Open the saved result to inspect the patch and check details.

The supported repair route is offline Lua work that Workbench can investigate and
check in the committed addon and its existing test framework. Source discovery is
performed anew for each report; it is not limited to the flight-path acceptance
files. Reading relevant code does not grant permission to edit it.

Results stay local. They do not modify the installed game addon, publish a change,
or create a PR. Offline Lua/game-API fixtures do not establish in-game correctness.
Features, general inspections, external simulators and broader automation remain
unfinished; their requirements have not been waived.

## Providers and recovery

The app offers Qwen alone, Codex alone, and an explicitly enabled switch in either
direction when the first provider is unavailable **before generation**. See the
current readiness record for which real-provider cases have passed. Partial or
uncertain generated work does not authorize a cross-provider replay.

Codex uses the existing signed-in subscription connection with the recorded
included-allowance policy. Eligibility reads are snapshots, not a verified per-turn
billing lock. API keys, purchases, extra credits and automatic reload are not used
or authorized. Missing eligibility information holds the operation.

Reloading a result or restarting the app must preserve it without running the
task again. Failed and uncertain attempts remain in history. A different scoped
repair attempt needs valid current task/run bindings; ordinary development of
Workbench itself does not need renewed engineering permission.
