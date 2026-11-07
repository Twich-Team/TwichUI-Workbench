# TwichUI Workbench

Current delivery authority and demonstrated state are in the root
[philosophy](PROJECT_WORKING_PHILOSOPHY.md),
[requirements](TWICHUI_WORKBENCH_REQUIREMENTS_v1_1.md),
[standing owner direction](STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md) and
[handoff](docs/CONTINUATION.md). Older milestone descriptions below preserve
their historical scope; they do not override the current installation, LAN,
billing or autonomous-engineering decisions.

A local, fixture-backed application baseline for reviewing TwichUI behavior.
Open a task, review each expected behavior, save a decision, and watch a separate
Python worker apply it. Saving first produces a **pending** receipt; the actual
outcome appears after revalidation. Live pilot writes are unavailable; Milestone 2 offers an opt-in read-only projection adapter.

Requires Python 3.14 and Node 24 (minimum Node 22.12). From this repository:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm ci
npx playwright install chromium
npm run dev
```

Open http://127.0.0.1:5180. Development starts the API on loopback 8787 and a
separate worker, migrates `.local/workbench.sqlite3`, and seeds the example only
if it is missing. Ctrl-C stops the owned processes. Port **8765 is reserved for
the existing pilot**. No service is installed or restarted.

```sh
npm run check        # contracts, TypeScript, production build, Python lint, schema drift
npm test             # disposable backend tests + frontend behavior tests
npm run test-e2e     # actual API/database/browser/worker workflow
npm run test-visual  # desktop/phone/long text/states + reviewed screenshot baselines
npm run verify       # all of the above
```

Tests use temporary databases and sanitized fixtures. The browser harness controls
separate worker invocations so pending, restart and stale states are deterministic;
`npm run dev` runs the worker continuously. Browser ports 5280/9187 are exclusive
to acceptance, and the harness refuses to reuse an existing server.

If Chromium needs system libraries, use your existing unprivileged browser runner
or install prerequisites through your normal host workflow. This repository does
not request root access. The already available runner on this host is documented
in [verification](docs/verification.md).

To run the built frontend without a Node server (local fixture preview only):

```sh
npm run check
PYTHONPATH=backend .venv/bin/alembic upgrade head
PYTHONPATH=backend .venv/bin/python -m workbench.seed
PYTHONPATH=backend .venv/bin/python -m workbench.worker
# In another terminal:
PYTHONPATH=backend .venv/bin/uvicorn workbench.api:app --host 127.0.0.1 --port 8787 --no-proxy-headers
```

Open http://127.0.0.1:8787. The backend serves only built frontend assets and API
routes. Do not expose these local fixture launch modes through a LAN/proxy.
Production authentication, deployment, backup operations, multi-host requirements
and a future database choice remain undecided. SQLite is a disposable single-host
development baseline on local disk, not a production commitment.

For autonomous milestone work, start with [the execution contract](docs/EXECUTION.md),
[one implementation plan](docs/PLAN.md), and [current progress](docs/PROGRESS.md).
Follow the root [standing owner direction](STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md)
and [philosophy](PROJECT_WORKING_PHILOSOPHY.md). Connected delivery work is already
authorized; former campaign and routine renewal gates are superseded. `AGENTS.md`
and `CLAUDE.md` point to the same active references. Setup itself grants no new
billing, disclosure, publication or system privileges.

On this host, the noninteractive wrapper discovers the existing toolchain:

```sh
python3 scripts/wb.py verify
python3 scripts/wb.py dev --disposable
python3 scripts/milestone.py --preflight
```

The milestone launcher is deliberately blocked while a shared Codex daemon is
present because it cannot verify that daemon's active-session state. Read the
execution contract before approving or launching work. Local status files are
not phone notifications.

See [ownership and compatibility](docs/ownership.md), [design/provenance](docs/design.md),
and [verification evidence](docs/verification.md). The implementation has no model,
GitHub publication, live-write, allowance, scheduling or planner adapters.

## Milestone 2 local views

Work summarizes current observations, actionable decisions and pending application.
Backlog supports open/all records, literal search, small pages, retained branch
expansion and record details. History separates saved decisions, worker outcomes
and dated projection updates. Local examples are clearly labeled; they are not
live maintenance activity.

By default, Maintenance shows unavailable. To enable the existing pilot's bounded
**read-only** projection while keeping development writes disposable:

```sh
WORKBENCH_PILOT_DB=/home/rich/twichui-pilot/dashboard/state/backlog.sqlite3 python3 scripts/wb.py dev --disposable
```

Open http://127.0.0.1:5180 and select “Maintenance · read only.” This does not
install/restart a service, migrate data, or enable live reviews. Stale or missing
observations never establish current inference/eligibility. API routes `/api/work`,
`/api/backlog`, `/api/records/{source}/{id}`, `/api/history` expose only selected
sanitary display fields. Ordinary E2E always overrides the pilot path with a
disposable synthetic database.

Milestone 2 was implemented directly in the existing chat; the standalone launcher
and its 120-minute timeout/status-file mechanism did not supervise that session.
See the current evidence and explicit device/live limitations in PROGRESS.md.

## Disposable acceptance review

Build/check, then serve the built frontend and API together on loopback 8787:

```sh
python3 scripts/wb.py check
python3 scripts/wb.py dev --disposable --built
```

This starts a separate fixture worker, uses a temporary DB, and stops its owned
processes/removes that DB on Ctrl-C. No Vite server is exposed. Open
http://127.0.0.1:8787/. For an existing SSH connection from another device:

```sh
ssh -N -L 8787:127.0.0.1:8787 rich@10.0.0.9
```

Open that same loopback URL on the tunneling device. An iPhone needs an existing
SSH client capable of keeping a local forward accessible to Safari; a computer's
loopback forward is not reachable from the phone. No such iPhone client/device
acceptance is established. No LAN bind, firewall change, proxy route, SSO trust or
public endpoint is enabled. See PROGRESS.md for the current running instance.

Local examples offer Simple review, Mixed records (37), Feature hierarchy,
Reviews and recorded outcomes, and Empty catalog. Selection stays in this tab.
Scenarios use the real catalog APIs; the same real fixture review remains writable
and saved/pending/applied outcomes come from the DB/worker. Demo records do not
represent live work or provider usage. Maintenance mode ignores demo selection.
New intake work has explicit dependency edges, separate from hierarchy. Older imported records have only the dependency facts their source retains. Missing facts stay unknown.

## Operating controls and monitoring

`/settings` edits only resource chart default range (1h/24h/7d) and display refresh
(15/30/60s). Values are validated and revisioned in the application DB. They apply
immediately and never change worker scheduling or model execution.

`/monitoring` defaults to observed host data, showing unavailable unless explicitly
configured. For a local disposable review using existing read-only pilot sources:

```sh
WORKBENCH_RESOURCE_DB=/home/rich/twichui-pilot/dashboard/state/history.sqlite3 \
WORKBENCH_POLICY_DB=/home/rich/twichui-pilot/dashboard/state/settings.sqlite3 \
python3 scripts/wb.py dev --disposable --built
```

The existing pilot collector owns sampling/retention; Workbench opens the databases
read-only and runs no collector in page requests. ECharts 5.6.0 is copied from the
pilot's verified installed vendor bundle with its license and provenance. Demo
telemetry is explicitly simulated and separate. Operating policy is read-only;
saved and last-acknowledged values do not confirm the current worker identity.

## Intake and planning

Work now offers **Report an issue**, **Request a feature**, and **Intake & planning**
(`/intake`). Original requests save before worker processing. Issues retain optional
literal Lua errors and can ask for specific clarification. Feature proposals have
immutable revisions, evidence, scoped acceptance and applicable mockup/reference
attachments. Decisions save a receipt before the worker applies them. Applied
approval queues a separate, idempotent decomposition job in the existing Backlog.
Implementation, existing holds and publication remain separately controlled.

Disposable development and browser tests explicitly use **Demo planner**. Demo
responses prove the real database/job/review path; they are not observed research.
Ordinary runs default to planning unavailable and keep submitted input when planning
fails. Explicit retries are limited to three attempts; no paid provider is invoked.

For an isolated built preview while another review occupies 8787:

```sh
python3 scripts/wb.py dev --disposable --built --review
```

This uses the already allowed loopback address http://127.0.0.1:9187/, a separate
worker and temporary database. It does not replace the prior preview, change access
configuration or preserve disposable submissions after shutdown. See PROGRESS.md for
the retained review's identity-safe shutdown command and observed process lifetime.

Local-only planning is an operator opt-in using `WORKBENCH_PLANNER=local` and
`WORKBENCH_LOCAL_KEY` pointing to the existing local service's private key file.
Never put that key in a browser, request or repository. The adapter uses the fixed
loopback llama.cpp endpoint, nonblocking existing maintenance/model locks, bounded JSON,
a cancellable planning deadline and actual returned model identity. It never restarts/replaces the model.
`WORKBENCH_RESEARCH_ROOT` optionally selects an existing source checkout for bounded
read-only evidence; otherwise research context is limited to product documents.
No source instruction or pasted Lua is executed. See ownership.md for exact budgets.
The closeout observed a validated local proposal in 50.574 seconds after diagnosing
the former 30-second cutoff. A content audit found an unsupported summary claim,
so research grounding is not fully accepted; see WB-15 in PROGRESS.md. `scripts/planning_smoke.py` is an explicit
isolated operator diagnostic, never part of ordinary tests.

For additions to existing Lua regression tests, select **Prepare a Lua
regression-test task** on a feature request. Describe the behavior, enter observable
expected results, and name up to three existing `tests/test_*.lua` files (in the
description or path field). The existing worker prepares deterministic scope and, when privately configured,
requests grounded scenario data through the existing planning adapter. No per-task
template registration is required. An operator must configure an
owned TwichUI checkout through the existing `WORKBENCH_RESEARCH_ROOT`; preparation
uses its committed HEAD and ignores dirty working files. Missing or ambiguous
evidence is reported for clarification, with candidate paths left unselected.

The proposed contract records repository/base, behavior/acceptance, writable test
paths, unrun verification commands, independent-check requirements, resource limits
and the draft destination `Twich-Team/TwichUI:main`. Scope approval is separate.
Scope preparation grants no execution or publication authority. Independent
semantic expectation review remains required in that same scope review. After its
applied approval, the application worker durably dispatches the existing bounded
scenario compiler and independent Runtime/profile checks, then registers a certified
offer. This connection is verified with controlled planning responses and Lua. Real
model-policy/runtime acceptance and installation remain unfinished. This is
not a generally usable task-to-PR service.
Local base inspection does not verify the current remote branch or live-game behavior.

For bounded browser verification, use `python3 scripts/wb.py test-e2e --grep 'title expression'`
to select titles while retaining the ordinary evidence budget and fresh output allocation. `test-visual` also accepts `--grep`; other commands reject it.

Attachments are private application-database records bound to a specific proposal.
Supported: non-interlaced PNG (1 MiB, 4096 pixels per axis, 8 megapixels) and UTF-8
plain-text supporting evidence (1 MiB). Implementation approval of visual or
unclassified impact requires both a proposed mockup and an in-game-context PNG.
Relevance/authenticity is a human review responsibility; presence is not proof of
implementation. An explicit research-only revision needs its own approval and
excludes implementation. No paid image generation, game capture or simulator
rendering is provided by this milestone. Tests use labeled synthetic artifacts.


Milestone 3 closeout: generated demo task titles use concise task labels, preserving
scope instructions and original user text in details. A representative synthetic
guide tracking proposal is in `fixtures/intake/guide-status.json`, with locally
rendered SVG/PNG mockup/reference fixtures and three distinct dependent subtasks.
Proposal review leads with the experience, scope, acceptance, previews, uncertainty
and decisions; raw provenance remains available in collapsed disclosures. Issue
triage completion never means root cause established.

The local provider now discovers the configured identity from the existing service,
uses a planning-only absolute deadline (default/cap 120s; operator environment
`WORKBENCH_PLANNING_DEADLINE_S` accepts 15–120), and closes the active socket on
worker shutdown or deadline. Recovery leases are 150s. API/session timeouts are
unchanged; no SQLite writer transaction is held during inference. Failure categories
and safe timing/model metadata are durable and inspectable under job provenance.
The current WB-15 diagnostic budget is exhausted: two substantive approaches total,
including the original failed smoke. `scripts/planning_closeout_smoke.py` has a durable
one-run guard and must not be reset to obtain another inference in this closeout.
Exact request/response material and the observed DB stay private under `.local`.


Browser evidence preservation: `test-e2e`, `test-visual` and `verify` now allocate
fresh `artifacts/runs/browser-*` directories (screenshots/workflow data plus
`results/` and `report/`). Earlier milestone directories are historical evidence.
Visual reference images remain in `e2e/*.spec.ts-snapshots`; do not update them merely
to pass a run. Existing CI's `artifacts/` upload includes the new run directories.

Evidence admission uses actual physical space with a recovery reserve. The old
512 MiB cumulative delivery limit is superseded. Closed owned browser evidence
is retained or independently restored and verified before matching originals
are pruned; databases, recovery copies and unique failures remain protected.
The configured watermark triggers housekeeping, not an execution stop. Inspect
storage and the archive index with:

```sh
python3 scripts/evidence_archive.py status
```

Standing authority covers routine verified retention of owned temporary evidence.
For a deliberate manual archive, check ownership, references and preservation
requirements first. Choose a new archive ID and list only eligible **closed**
workspace-relative directory paths:

```sh
python3 scripts/evidence_archive.py archive ARCHIVE_ID artifacts/runs/CLOSED_RUN/CLOSED_OUTPUT --remove-originals
python3 scripts/evidence_archive.py verify ARCHIVE_ID
python3 scripts/evidence_archive.py lookup artifacts/runs/CLOSED_RUN/CLOSED_OUTPUT/FILE
python3 scripts/evidence_archive.py restore ARCHIVE_ID --destination /absolute/isolated/restore-root
```

Without `--remove-originals`, archive only copies and verifies. A repeat with the
same ID/paths resumes an interrupted operation; changed sources, corrupt archives,
symlinks, special files and conflicting destinations are refused. Byte-identical
files, modes, modification times and empty directories round-trip. Original-path
mappings and SHA-256 hashes live in each external `manifest.json`, with a hashed
receipt in `artifacts/archive-index/`. Default storage is
`~/.local/share/TwichUI-Workbench/evidence-archives/`; retain both it and the receipts.
Partial interrupted copies are retained beside archive data. No automatic retention
cleanup is installed. The older Lua-preparation archive retains its original
`ARCHIVE.json` mapping and is not silently converted into this new index.

Omitting `--destination` restores into the original workspace, provided the counted
budget permits it. Restore never overwrites different bytes or removes an archive.
The tool serializes its own operations; it does not lock active test writers.
Use only closed evidence. Archived browser reports can be restored in isolation
with their original relative paths. Databases, approvals, candidates and baselines
are not cleanup targets.

The local source baseline excludes runtime outputs, stores, credentials, private
configuration and dependencies. Generated OpenAPI/TypeScript contracts are retained
locally but excluded from Git; `npm run contracts` recreates them from the committed
backend schemas before `check`. Authored fixtures, artwork and screenshot reference
baselines are source inputs and remain versioned. `tools/codex-workbench.toml` is
the repository's invocation-only safety policy, not installed host configuration;
it contains denied-path rules, with no authentication material.

### Bind a reviewed contract to a certified offer

Reusable regression preparation compiles declarative behavioral
examples into the frozen oracle and positive/vacuous/wrong-behavior controls. It
supports one existing test file, up to three scalar observations through a returned
module function or the harness's exported client functions. No per-task Lua package,
registered recipe or hand-authored passing solution is required for those shapes.
The browser separately confirms the exact fixture/operation/inputs/expected outcomes;
model authorship and that confirmation do not replace independent control checks.
Missing examples and failed expectation checks have specific holds.

`trusted_preparation.prepare_regression` remains the internal entry through existing
Runtime/profile/offer owners. The existing application worker now dispatches it from
the existing planning-job journal after independent scope/expectation review and
single-task decomposition. No individual user/developer preparation command is
needed. Controlled tests cover module-return and client-state requests; this is a
bounded supported subset, not arbitrary Lua planning. Literal committed module /
client loaders and explicit exported receiver mappings are screened conservatively;
unknown or dynamic mappings produce specific clarification/holds. Generation is
proposed intent; it cannot certify itself or supply permissions.

Installation input is a private (0600), existing absolute JSON file selected by
`WORKBENCH_PREPARATION_CONFIG`, shared by API and worker. It contains:

```json
{
  "version": 1,
  "database": "/absolute/existing/workbench.sqlite3",
  "database_identity": [123, 456],
  "repository": "Twich-Team/TwichUI",
  "allowed_classes": ["lua-regression-tests"],
  "output_root": "/absolute/existing/owned/preparation-output",
  "identity": {"name": "Configured author", "email": "configured@example.invalid"},
  "scenario_generation": true
}
```

The example device/inode numbers are placeholders: installation must use the actual
store's `st_dev`/`st_ino`. Root and identity are trusted installation inputs, never
request/model output. This config enables no implementation, publication, provider
fallback or extra spending. The existing `WORKBENCH_RESEARCH_ROOT` and planner
adapter configuration still apply. Grounded local planning requires a saved,
enabled preferred local model with effort unspecified in Models. The worker
records the selected revision and identifier before transport. The adapter requires
that identifier from both `/v1/models` and the structured response. Full routing,
capacity and real usage acceptance remain open. This source was checked with
controlled responses and transport.

Scenario response and reviewed contract bind original request, retained feedback,
committed base/source hashes, schema and preparation policy. Changed policy/base
requires fresh preparation/review. The job stores a claim before checks start;
retries/restarts cannot duplicate preparation. Complete independently certified
artifacts reconcile without rerunning checks; partial/unknown work holds for
inspection. This bounded safe recovery does not complete general retries/fallback,
runtime lifecycle, unattended delivery consistency or installation. `prepare-offer`
below remains an operator tool for existing evidence, not the ordinary workflow.
See `docs/CONTINUATION.md` for exact boundaries.

Trusted local operators can now register an **already independently certified**
preparation through the supported wrapper, without database edits or an owner
instruction script:

```sh
WORKBENCH_RESEARCH_ROOT=/absolute/owned/research-checkout \
python3 scripts/wb.py prepare-offer \
  --database /absolute/existing/workbench.sqlite3 \
  --intake-id INTAKE_UUID --task-id TASK_ID \
  --scope-approval-id APPLIED_SCOPE_DECISION_UUID \
  --evidence-root /absolute/owned/certified-runtime
```

The store and evidence root must exist, belong to the local operator, have no shared
write access or symlink traversal, and match the current reviewed contract and clean
research base. This command re-evaluates existing certification and records only an
idempotent offer. It does not create/migrate a store, run commands, certify ordinary
request input, grant execution or activate a service. There is no HTTP preparation
endpoint. See [verification profiles](docs/verification-profiles.md) for the existing
independent oracle/control requirements.

In the request's **Resulting work** section, open the eligible task offer, inspect
scope/base/paths/checks/limits and draft destination, and explicitly approve the
combined scope/run/draft-PR budget. The decision is saved first and applied by the
existing worker; retries/reloads retain the same receipt and cannot create another
attempt. Missing or stale certification holds the offer. An installation without
one-off execution capability explicitly says execution is unavailable; a saved
non-activating budget never turns into activation later.

Independent fixture/behavior review and operational configuration/authorization
remain required. Supported scenario controls need no hand-authored solution; other
oracle shapes still need trusted preparation. Bug-fix typed-contract preparation
remains manual. Profile
selection, evidence certification, offer creation and human execution authorization
are distinct operations. This source capability does not install itself on port9487.
