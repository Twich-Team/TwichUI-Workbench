# Proposed policy decision: unrecoverable, tool-free planning turns

Status: PROPOSED, NOT AUTHORIZED. The existing exception remains limited to
18bbf5d0-c5e2-4557-936b-126b24b4f1f4's second turn.

## Concrete current case

The fresh flight task9961b8d4-5167-470d-85ff-9be02f8daff1/model
eb72eb02-c968-4c57-b51a-5106bf7e6236 began normally in verified52c54af.
First Codex turn completed with31352input/149output/35reasoning/31501total tokens.
The second emitted an inspection request followed by an extensive whitespace loop,
without closing the JSON. It returned18893agent-output bytes before a generic
protocol hold. The private prefix retains three read requests, then mostly
whitespace. No implementation, approved plan or file effect occurred. Do not reuse
that partial response. The precise protocol guard was not retained; byte exhaustion
is a supported code-path hypothesis, not an established historical observation.

Cancellation was not acknowledged. The recovered thread/turn IDs are recorded,
but supported thread/read and thread/turns/list report thread not loaded; a separate
thread/resume reports no rollout found for the exact owned thread. These diagnostics
used the pinned CLI with networking disabled, no model turn/source transmission or
login change. They establish that local ephemeral state is unavailable; they do
not prove remote cancellation, account denial or absent subscription access.
The CLI process is gone. Final provider outcome and final usage remain UNKNOWN.
Private records: C/outcome-52c54af/attempt-29-accounting.json and codex-evidence.
The older approved18bbf5d0 quarantine and all records remain unchanged.

## Connected correction, not a request for engineering permission

The source correction now:
- Gives cancellation a separate bounded2MiB read reserve and10second deadline,
  rather than reusing an exhausted generation budget. Invalid stale framing may
  be skipped within that reserve; only a valid same-thread/same-turn terminal
  receipt establishes completion. Missing receipts still remain uncertain.
- Stops excessive outside-string whitespace after1024characters. JSON string/Lua
  content spacing is preserved. No partial JSON is completed, accepted or executed.
- Adds exact guard/byte/terminal-status diagnostics; keeps strict JSON decoding.
- Uses an explicit read/search tag with required nullable unused fields on the
  Codex wire, removing the ambiguous heterogeneous inspection-item branch. Fully
  completed replies map deterministically to the unchanged strict local schemas.
  This is a corrective hypothesis for the formatter loop, not proof of an
  unsupported provider schema feature. Source/scope/authorization checks remain.

141affected protocol/format/investigation tests pass, including separate cancellation
reserve, malformed framing, whitespace inside/outside strings, terminal accounting,
explicit-wire ambiguity and source permission. Integrated/native/final verification
continues independently before deployment. No extra live generation has occurred.

## Requested single policy decision

Permit permanent quarantine of this current second turn AND future qualifying
read-only planning turns during this standing delivery effort, only when ALL hold:

1. The original operation was model-only planning/investigation. No editing tools,
   shell, repository writes, publication, implementation execution or external
   side-effect tools were available or started.
2. No plan, task run approval, implementation offer or successful checked case was
   produced or applied from the uncertain response. Never quarantine a completed
   successful case merely because a local receipt is missing.
3. The owned local producer/connection is closed and no owned generation process
   remains. Attempt supported same-identity status/cancellation recovery when
   available. If local execution itself remains active/uncertain, hold.
4. Retain original task/model/job records, observed usage, partial-output evidence
   and diagnostics. Mark final provider outcome/usage unknown, never zero, bounded
   by an invented ceiling, successfully cancelled or remotely settled.
5. A private immutable receipt binds the exact historical records, identities,
   operator policy authority, evidence and no-implementation checks. It grants no
   authority to alter the historical accounting. Other uncertain activity holds.
6. Ignore all partial output from the quarantined turn. It cannot be a plan,
   approval, patch or fixture. Reuse only independently valid committed source
   evidence and completed checks with provenance.
7. Record a concrete corrective/diagnostic change and verify it locally before
   renewing. Do not repeatedly send unchanged failing requests or hide failure.
8. Fresh task/consent/plan/run bindings are required through the delegated normal
   browser workflow. Keep the explicitly selected provider; this is no
   post-generation fallback or silent provider switch.
9. Preserve usage accounting across tasks; do not reset totals or old budgets.
   Each new operation remains bounded/observable and cancellation remains supported.
   Former campaign clocks/allocations/revision caps stay superseded.
10. Every fresh Codex turn must pass the existing two included-usage/account reads,
    reserve, credit, model and sign-in checks. No API billing, purchased/additional
    credits, purchases, reload, subscription change or billing-policy relaxation.
    Allowance checks remain snapshots, not per-turn billing guarantees. Unknown
    historical included usage is not permission for a charge.
11. Keep existing source-disclosure, task write scope, verification, credentials,
    network, system-permission, history/recovery and publication boundaries. No
    source, installation or addon-writing permission expands with this policy.
12. This applies only within the already assigned delivery outcome until completion
    or owner revocation; accepted historical quarantine receipts remain preserved.
    It does NOT cover uncertain implementation/file/command/publication activity.

This explicitly permits fresh work despite the defined historical model uncertainty;
it does not mislabel that uncertainty as resolved. It replaces repeated one-turn
quarantine decisions for this narrowly defined class, not meaningful task approvals.
Until approved, no generic quarantine mechanism is active and the current model
attempt remains held. Engineering/testing proceeds under existing standing authority.
