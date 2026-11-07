# Campaign hold after the first flight-path investigation

Following PROJECT_WORKING_PHILOSOPHY.md, preserve the working-application
outcome and distinguish a routine repair from permission to repeat an
unexplained failed generation. This supplements the unchanged campaign
approval at `7b5963a`; it does not replace its limits or open a new clock.

## Observed result

The owner confirmed availability and the campaign opened at
`2026-11-07T04:35:45.612Z`. New admissions end at `18:35:45.612Z`, with
restoration complete by `20:35:45.612Z` on the same boot. Initial
candidate `cbddecf` passed its archive, independent restore, original-path
lookup, checked history copy, service, isolation, TLS, browser and LAN
gates. The previous separate-device LAN evidence remained applicable.

Attempt 1 submitted the real flight-path report through the normal app.
Intake `f3f45977-e4da-4563-b260-4cf565661273`, planning job
`6f24d7a4-6c24-4a68-a39c-879fc0562a80`, model attempt
`8d4e906c-f744-4cac-bfb9-9f127a423f5d`.

Qwen returned under its deadline. The persisted diagnostics identify
`generation_state=finished`, actual model `pilot-qwen38-27b`, 3,372
response-envelope bytes, and a subsequent `schema_validation` rejection.
Usage records 4,347 prompt tokens, 608 generated tokens and 4,955 total
tokens. The model attempt and job are finished. There is no proposal,
plan decision, implementation, new execution request, or Codex turn in
this campaign. No original flight-path reproduction or fix was produced.

The worker discards the rejected structured response and the Pydantic
validation locations. `retained_failure` preserves a terminal failure
marker, not response contents. Read-only database inspection and source
inspection cannot recover the particular invalid field. The exact reason
for this rejection is unknown. It is not established as a transport,
partial-output, model-identity, SQLite, or fixture-loader failure.

LAN ingress was closed immediately and only the owned rule removed.
The prescribed restoration passed: original loopback assets, original
79-row history, checked backup, original firewall, absent candidate
services/socket/drop-in, normal authenticated Qwen and unchanged Codex
authentication/configuration metadata. The failed 84-row campaign store,
archive, independent restore and diagnostics remain preserved.

Private receipts: `/home/rich/.local/share/TwichUI-Workbench/delivery-campaign-7b5963a`.

## Offline correction and remaining uncertainty

The development change retains bounded schema locations, fixed error types,
an error count and planning-schema hash in the existing diagnostic record.
It excludes provider values, unknown keys, error messages, inputs and
context. Controlled tests prove terminal failure, no proposal or task,
no automatic replay, bounded output and redaction. This corrects missing
diagnostic evidence; it does not prove a correction to the rejected plan.
Source checkpoint `ab25071` passed `wb.py check`, all 1,146 default backend
tests, 25 frontend unit tests and the seven focused bug-workflow tests.
The three opt-in backend checks and Chromium/visual journeys have not
been rerun for this diagnostic change. Earlier initial-candidate evidence
remains scoped to `cbddecf`; it does not seal or activate this source.
It is not installed. Further verification and any candidate revision
must satisfy the existing campaign gates before activation.

The campaign requires a "demonstrated connected cause and verified
correction" before renewing a failed case. It also says that a generic
failed row and token counts do not establish eligibility. The first
result cannot presently meet the demonstrated-correction condition.
Live work is held. No aggregate counter can override this condition.

## Specific policy decision, if the owner chooses to continue

Permit **one fresh flight-path attempt** despite the unrecoverable cause
of attempt 1, solely after a diagnostics revision has passed all required
verification, staging and deployment gates. This is an explicit exception
to the demonstrated-correction prerequisite for this one finished schema
rejection. It is not a claim that the response has been fixed. The next
investigation may fail again.

Charge it to the existing campaign as attempt 2 and a second Qwen bug
allocation. Do not replay or overwrite attempt 1, recover its nonexistent
plan, reset the clock, enlarge any budget or reuse a task approval.
Keep the exact report, nine read paths, six possible write paths, three
criteria, model, source base, independent review, ordinary browser decisions
and task limits from the approved campaign. No flight-path source goes
to Codex. Qwen planning remains one request, 120 seconds and 2,600
requested generated tokens. If it yields a valid independently reviewed
plan, the existing separately delegated plan/run decisions and bounded
implementation journey may proceed under the unchanged campaign.

A new invalid, partial, provider-failed, uncertain or active result holds;
this exception creates no automatic further renewal or provider switch.
All source, isolation, history, restoration and billing safeguards remain.
No additional allocation, spending, API access, publication or addon
installation is requested. Until a specific owner decision arrives,
the exception is unapproved and all live work stays held.

Remaining ceilings are conditional capacity, not eligibility: seven
attempt slots, three revision slots, four local task allocations, two
bug allocations, 109 local requests, 132,692 generated local tokens,
four Codex supporting allocations and eight Codex turns. The original
clock and all intersecting admission/restoration limits remain in force.

The real bug outcome, four provider cases, A8 and broader product remain
unfinished. No live Codex bug repair, game acceptance or draft PR delivery
is established.
