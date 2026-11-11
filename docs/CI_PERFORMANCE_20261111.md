# CI performance — November 11, 2026

The owner authorized measurement and safe optimization during the current campaign.
UI/cleanup delivery is already complete at private build1e848a7/deployment110. This
work changes development verification, not the installed app or legacy services.
Keep every required test, assertion, independent check, isolation rule and full
release decision. No paid model request, new runner service or billing change.

## Measured baseline

Three comparable recent successful runs (43057614742,43049281501,43042759499) took
29m21s,30m10s and28m42s from workflow creation to final job completion: median
**29m21s**. Runner queue/startup was3–5seconds. The45-minute workflow timeout was
not the normal observed duration. The subsequent documentation-only cfec47e run
43064461091 also passed and took25m56s with the same application code as1e848a7.

| Stage | Recent observed duration |
| --- | --- |
| Python interpreter setup | 0s |
| New Python environment | 3–5s |
| Locked Python packages | 9–10s |
| Node setup / npm package cache | 2s |
| npm ci | 5–6s |
| Signed mirror preflight | 0–1s |
| Chromium plus OS dependencies | 27–48s |
| Lua and reference fonts | 4–7s |
| Build, contracts and schema | 12–13s |
| Backend and frontend command | 10m52s–11m38s |
| Browser workflows | 15m33s–15m54s |
| Screenshot regression | 46–48s |
| Artifact upload | 10–11s |

The latest release's pytest duration was662.75s; Vitest reported approximately1.5s
of frontend suite work. The command's678s wall time also includes process/startup
and reporting overhead. Splitting these commands gives separate hosted step timing.
Chromium installation is a minor normal cost, not the principal bottleneck.

Infrastructure failures are separate: run43036133216attempts1/2spent608s/606s in
Chromium's OS setup and run43040222333spent566s there, failing before application
checks because Ubuntu archive DNS resolution failed. Those three failed job
attempts consumed roughly31runner-minutes. The already-verified MIT mirror with
Ubuntu's existing signature keyring fixed that failure; no new mirror or trust key
is introduced here. Run42964855630reached npm test and failed because its administrator schema
fixture assumed the server UID instead of the hosted runner UID. That test
fixture was subsequently corrected; the product ownership check stayed intact.
Application/test failures are not classified as network setup failures.
Private raw run/job/step timestamps and logs are retained under.local/ci-performance.

## First implementation checkpoint

- Run independent verification groups on separate standard GitHub-hosted VMs.
  Two backend partitions retain serial pytest and all original fixture migrations.
  Four official Playwright shards retain one worker per VM; fixture databases,
  ports and resets cannot interfere across runners. Visual checks run separately.
- Preserve complete source-based test inventories. Local collection proves the
  backend partitions form an exact disjoint union; browser partitions cover all
  131cases exactly once. No test, assertion, expected screenshot or retry changed.
- Keep the existing final `verify` check as a fail-closed aggregate. Any failed,
  cancelled, missing or skipped required group prevents a full-release receipt.
  Fast/partial feedback cannot authorize deployment. Every matrix member matters.
- Cache pip package downloads using requirements.lock, retaining a fresh environment
  and locked install. Preserve existing npm download caching and npm ci integrity.
  Build frontend once, transfer the same-run artifact to test jobs, and keep failure
  reports/receipts for14days with separate shard/attempt names.
- Preserve signed OS dependency installation and standard hosted runners. Browser
  binary caching was evaluated but is not initially added: its normal setup cost
  is small, OS dependencies still need installation, and restoring the cache can
  cost as much as downloading it. No self-hosted runner or dependency service.
- Add per-test backend durations/JUnit, browser JSON reports and a full acceptance
  receipt tied to the exact commit. Hosted measurements, coverage parity and any
  shard imbalance determine whether further changes are worthwhile.

[Playwright's supported sharding](https://playwright.dev/docs/test-sharding),
[its browser-cache guidance](https://playwright.dev/docs/ci#caching-browsers) and
[setup-python package caching](https://github.com/actions/setup-python/blob/main/docs/advanced-usage.md#caching-packages)
are the primary implementation references. Parallel setup may add runner-minutes;
report both wall-clock improvement and measured aggregate cost rather than promising
spending savings. The5–10minute objective remains a target, not a weakened gate.

Next: verify this first hosted parallel run, measure actual coverage/counts and
wall time against the baseline, repair any real isolation/setup issue, then add
clear fast documentation feedback without letting it satisfy release acceptance.
Do not cancel a run needed for an active release decision or reuse passing results
across different source/merge inputs. Current private110stays unchanged.

Local first-increment checks passed: static/contracts/build/schema,18CI gate tests,
exact backend/browser collection unions and one real browser shard (42passed,
2existing skips). The added CI tests also exercise a real controlled subprocess
pass/failure and refuse a receipt for another checkout. No application test or
expected screenshot was changed. The initial reporter syntax error was found
before tests started and corrected; actual shard execution and JSON reporting
then passed. Hosted timing is pending; no speedup is claimed before measurement.
