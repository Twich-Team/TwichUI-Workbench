# Milestone 1 verification — 2026-11-03

Historical Phase 1 evidence. Current state, fresh setup checks and acceptance gaps
are maintained only in [PROGRESS.md](PROGRESS.md).

`npm run verify` passed against the final implementation. This is an executable
fixture workflow, not a mocked success screen. Browser writes go to the actual
FastAPI/SQLite API; tests invoke the separate Python worker against the same
throwaway database. Only deliberate network-failure and response-loss scenarios
intercept transport. No model or external-write implementation exists here.

## Exact checks and results

The host had Python 3.14.4 and the pilot's Node 24.22.1/Playwright Chromium files,
but no `node`/`npm` on PATH. An unprivileged tool directory at
`/tmp/twichui-workbench-tools/bin` points to that Node binary and npm 11.6.2,
acquired from the official npm registry. No shell profile, service or host package
configuration changed. A normal Node 24/npm installation can use the README commands.

Focused checks run during implementation:

```sh
.venv/bin/python -m pytest -q
PATH=/tmp/twichui-workbench-tools/bin:$PATH node node_modules/vitest/vitest.mjs run --config frontend/vite.config.ts
PATH=/tmp/twichui-workbench-tools/bin:$PATH npm run check
PATH=/tmp/twichui-workbench-tools/bin:$PATH npm test
```

Browser commands used the existing documented pilot runner, without privileged
changes or an alternative unrestricted host runner:

```sh
export PATH=/tmp/twichui-workbench-tools/bin:$PATH
export PLAYWRIGHT_BROWSERS_PATH=/tmp/twichui-dashboard-browsers
export LD_LIBRARY_PATH=/tmp/twichui-dashboard-libs/root/usr/lib/x86_64-linux-gnu
npm run test-e2e
npm run test-visual -- --update-snapshots
npm run verify
npm audit
```

The final verification was captured with exit-code propagation:

```sh
set -o pipefail
PATH=/tmp/twichui-workbench-tools/bin:$PATH PLAYWRIGHT_BROWSERS_PATH=/tmp/twichui-dashboard-browsers LD_LIBRARY_PATH=/tmp/twichui-dashboard-libs/root/usr/lib/x86_64-linux-gnu npm run verify 2>&1 | tee artifacts/verify.log
```

| Check | Result |
| --- | --- |
| Generated OpenAPI/TypeScript, TS check, Vite production build, Ruff | Pass |
| Alembic fresh migration + model/schema comparison in temporary DB | Pass |
| pytest | 29 passed |
| Vitest + Testing Library | 10 passed |
| Actual browser/worker workflows | 7 passed |
| Responsive/state/visual browser tests, including baseline comparison | 8 passed |
| npm audit, including development dependencies | 0 vulnerabilities |

Backend coverage includes commit-before-acknowledgment, optional notes and explicit
choices, replay before/after application, conflicting IDs/payloads, concurrent
submissions/processors, stale evidence before/after save, short and sustained
contention, bounded 503/Retry-After, pending worker recovery, true process exits
before/after commit, migration repeatability, rollback, foreign keys, backup/restore
integrity, exact Host/Origin/CSRF, ignored forwarded headers and bounded bodies.
One upstream Starlette/httpx deprecation warning remains; all tests pass.

Browser coverage includes navigation and Back, drawer opening/closing, focus
restoration, both decision outcomes, pending receipts and reload recovery, actual
API commit followed by deliberate response loss and identical retry, database
contention, crashed-worker recovery, stale review and fresh explicit resubmission,
background evidence changes, preserved input on failed refresh, resizing, missing
metadata, empty/loading/failed states and overflow. Widths: **320, 390, 768, 1024,
1440 px**. Overflow measurements are retained in `artifacts/overflow.jsonl`.

Initial failures were corrected: a missing Radix test wrapper, one malformed-JSON
test lacking Content-Type, test races that invoked a one-shot worker before commit,
and drawer focus restoration. Initial useful traces/screenshots/videos are retained
under `artifacts/initial-failures/`; the final verify run passes without retries.

## Rendered images actually inspected

The implementation agent opened and inspected the rendered desktop Work/detail,
applied/stale outcomes, phone Work/detail/review/pending, long-text review at
desktop/phone/minimum width, and empty/loading/failed screenshots. Drawer captures
use the viewport, so a full-page image does not misleadingly show content behind
a fixed modal. The six checked-in Linux/Chromium screenshot baselines were created,
visually inspected, and then compared by the final ordinary `test-visual` run.
Baseline creation alone was not counted as acceptance.

Useful local evidence (ignored runtime output, not source artifacts):

- `artifacts/verify.log`: final complete check output.
- `artifacts/screenshots/`: rendered Work, detail, review, pending, applied,
  stale, long text, metadata and loading/error/empty captures.
- `artifacts/overflow.jsonl`: widths, scroll widths and any offender elements.
- `playwright-report/` and `test-results/`: latest visual run report/attachments.
- `artifacts/dev-smoke.json`: successful Vite and built-frontend browser responses.
- `e2e/visual.spec.ts-snapshots/`: six sanitized, tracked regression baselines.

CI retains artifacts and failure traces/video/screenshots for 14 days. CI is
configured but has not run remotely: nothing was committed or pushed. Baselines
are Linux/Chromium-specific; other browser/OS/font combinations need their own
reviewed acceptance. Phone coverage uses Chromium viewports, not physical devices.

## Started, loaded and live-observed

`PATH=/tmp/twichui-workbench-tools/bin:$PATH npm run dev` was started locally:
Vite 5180, API 8787 and a continuous Python fixture worker. A real Playwright
smoke opened both `http://127.0.0.1:5180` and `http://127.0.0.1:8787`, navigated to
the review sheet on a 390 px viewport, and observed HTTP 200 and six radio inputs.
The second address served the built frontend directly from FastAPI. Those owned
development processes were stopped after inspection. Disposable acceptance servers
on 5280/9187 were also stopped by the harness. `.local/workbench.sqlite3` contains
only the seeded fixture, with no decision submitted by that development smoke.

The pilot listener on 8765 was read-only observed before/after this work. No pilot
HTTP data was loaded, no service restart or loader was invoked, and no live worker,
receipt, approval, pause, allowance, private artifact or addon/game file was changed.
No live model/GitHub/publication workflow was exercised. No hosting registration,
deployment, commit, push, PR or external configuration change occurred.

## Limits and next step

This milestone is fixture-only. SQLite and local trusted-loopback launch modes
are development choices; production authentication, deployment and database choice
remain provisional until multi-host/concurrent-writer needs are confirmed.

The smallest next milestone is a bounded **read-only compatibility adapter** for
one task and its review evidence, with canonical IDs, source ownership and freshness.
Keep the worker's current files authoritative. Live review writes require a later,
explicitly controlled ownership cutover and canonical-gate/reconciliation testing.
