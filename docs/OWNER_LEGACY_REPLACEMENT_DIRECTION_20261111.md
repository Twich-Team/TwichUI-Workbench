# Owner architecture direction: replace legacy maintenance — November 11, 2026

Authoritative owner instruction, recorded during the Backlog cleanup milestone.
Workbench is intended to fully replace the original TwichUI maintenance coordinator
once the required capabilities have been migrated and independently verified.
The legacy coordinator is transitional infrastructure, not a permanent peer.
This direction does not interrupt cleanup delivery or authorize live cutover now.

## Required end state

Workbench owns addon intake, automated discovery, planning, scheduling, provider
selection, implementation, verification, recovery, reviews and authorized draft
PR delivery. Reuse proven maintenance functionality where useful, through one
coherent set of authoritative task, scheduling, approval, resource and publication
owners. Do not operate competing queues, schedulers, approval systems, model
reservations or publication owners after cutover.

Preserve historical requests, execution evidence and relevant operating data.
Keep the existing local Qwen inference service as appropriate; retiring the
coordinator does not mean removing that service. Workbench's coherent resource
policy prioritizes owner-submitted work over lower-priority autonomous maintenance
where safe, without pretending interrupted work or usage has settled.

Only disable legacy execution after Workbench independently demonstrates equivalent
required capabilities and successful recovery, with a separately verified and
authorized reversible cutover. This instruction does not authorize deleting legacy
data, uninstalling services, replacing privileged owners, changing live schedules,
or prematurely disabling maintenance. After cutover, legacy maintenance must no
longer compete for Qwen or independently create or publish addon work.

## Initial dependency inventory: observed, not a completed capability audit

Read-only checks on November 11 found twichui-worker.service and twichui-llm.service
active. twichui-daily.timer still activates twichui-daily.service. These were only
observed; no unit, timer, queue, credential or operating policy was changed.

| Dependency | Present evidence | Replacement acceptance still required |
| --- | --- | --- |
| Legacy discovery and scheduled maintenance | Legacy batch directory contains discover.py, cycle.py and its durable operating files; daily timer remains installed | Enumerate enabled discoveries and schedules, qualify their required behavior and migrate them into Workbench-owned intake/scheduling without duplicate dispatch |
| Cooperative Qwen reservation and yielding | Workbench local_model_priority.py verifies the legacy worker/cycle identity and uses cycle.lock and queue/model-request.lock | Workbench owns the coherent priority/resource policy; retain exclusive generation, truthful interruption accounting and recovery, then remove the need for a competing coordinator |
| Inference service, authentication and restoration | Workbench real_lease.py still references the existing model service, environment, key location and cooperative locks | Preserve Qwen availability/authentication and service-owner recovery; migrate path/privileged dependencies only through a separately verified transition |
| Legacy task, review and publication state | Existing ownership documentation identifies legacy JSON/JSONL queues and review owners; the batch directory retains review and decision modules | Map identities, decisions, outcomes and publication receipts; preserve provenance and explicitly resolve pending/uncertain work before fencing any old writer |
| Maintenance recipes, simulator and independent checks | Legacy batch directory retains runner, simulator, qualification and publication modules | Inventory which capabilities are required, reuse qualified checks and independently demonstrate equivalent outcomes in Workbench; module presence alone is not proof |
| Workbench task-to-result delivery and recovery | Existing durable Workbench worker, bound approvals, provider choices, checked real results and draft receipts | Extend coverage to all required maintenance classes and recurring work; current real-task acceptance does not establish complete legacy parity |

This is the starting inventory. A dedicated migration milestone must inspect actual
enabled recipes, triggers, backlog/review states, leases and publication receipts,
then produce a capability-by-capability acceptance matrix with explicit remaining
gaps. Do not infer parity from filenames, passing UI checks or two real Qwen tasks.

## Roadmap and acceptance gates

1. Finish the current cleanup/private-preview milestone. Keep Feature Workshop as
   its own next product milestone; its parent/dependency structure must support
   future owner and autonomous requests in the same authoritative task system.
2. Complete the dependency/capability inventory and data identity map. Define each
   required input, trigger, provider route, check, recovery path, review and draft
   delivery boundary; classify retained-only history separately from pending work.
3. Migrate and test required discovery, recurring scheduling and maintenance recipes
   through existing Workbench task/approval/publication owners. Retained legacy
   adapters must not create an independent scheduler or competing task authority.
4. Independently demonstrate each required behavior, owner-work priority, restart,
   cancellation, interruption reconciliation, unavailable providers, failed checks,
   duplicate-trigger prevention, publication consistency and recovery. Use controlled
   fixtures where sufficient; any real paid/provider/external effects retain their
   existing authorization boundaries.
5. Rehearse a reversible cutover on copied stores: inventory/export and hash old
   records, settle or truthfully quarantine work, fence exactly one writer, transfer
   required pending work once with provenance, and verify counts/evidence/receipts.
   Prove rollback preserves newly recorded work and does not replay uncertain effects.
6. Present the verified capability matrix, complete data/recovery receipts, exact
   service/scheduling/publication changes and rollback plan for separate cutover
   authorization. No live disablement or privileged-owner replacement before it.
7. Execute only the authorized cutover, then verify one authoritative Workbench
   scheduler/queue/resource/publication chain. Demonstrate legacy cannot dispatch,
   reserve Qwen or publish, while the Qwen service and historical data remain intact.

No broad migration or retirement campaign is launched by recording this direction.
