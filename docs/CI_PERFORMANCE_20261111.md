# CI performance — November 11, 2026

The owner authorized measurement and safe optimization during the current campaign.
UI/cleanup delivery is already complete at private build1e848a7/deployment110. This
work changes development verification, not the installed app or legacy services.
Keep every required test, assertion, independent check, isolation rule and full
release decision. No paid model request, new runner service or billing change.

**Completed:** full hosted verification passed at362b84e and9850c40. The two
successful optimized runs have a7m59s median, compared with29m21s previously.
The detailed comparison and limits are below. Private build1e848a7/deployment110
remains installed; CI-only changes do not require an application cutover.

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
  131cases exactly once. No test removed, assertion weakened, expected screenshot
  changed or retry added.
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
pass/failure and refuse a receipt for another checkout. No application assertion or
expected screenshot was changed. The initial reporter syntax error was found
before tests started and corrected; actual shard execution and JSON reporting
then passed. Hosted timing is pending; no speedup is claimed before measurement.

## First hosted measurement and exposed test race

Run43071395042 completed in7m46s but **failed** release acceptance: all2253backend
checks (11existing skips),58frontend checks and128browser checks (3existing skips)
passed; one of12visual checks failed. Its captured page already showed the empty
workspace when the test tried to click Try again. Removing the failed route allowed
the real1500ms background poll to recover before that manual action. This is a
test synchronization race, not a screenshot difference or an application failure.
The final verify job correctly failed and emitted no release acceptance artifact.
This failed run is not included in a successful-run performance median.

The repair installs Playwright's browser clock before navigation, pauses polling
while the failed-state screenshot and recovery fixture are prepared, then resumes
immediately after the original Try again click. Original loading/error/empty
assertions, screenshot expectations, timeouts and retries remain unchanged. Three
fresh local repetitions and the full12-check visual suite passed. Static, generated
contracts, build and schema checks also passed. Updated full hosted verification
must pass before this checkpoint is accepted.

## Second increment: useful fast feedback without partial acceptance

The checks job already gives build/contracts/schema/frontend feedback before the
longer suites finish. Reporting-only pushes now have a separate lightweight
documentation workflow. Only explicitly listed README/status/continuation/plan/
progress/performance/history paths qualify. Unknown paths, code, tests, assets,
configuration and authority documents default to full verification. Every pull
request still runs the full suite, avoiding a missing required verify check;
workflow_dispatch also retains full acceptance for an exact candidate commit.
Mixed code/report changes run both workflows. No existing release decision changes.

The documentation job checks the whole changed range, not just the last commit,
and clearly states that success cannot authorize installation. It cannot emit a
full-release receipt. Only superseded documentation jobs can be automatically
cancelled; full runs remain available for active release decisions. Installing a
report-only commit still requires a full run for that exact commit. Passing results
are never reused for a different PR merge/head input.

Local workflow parsing checked complementary paths, complete PR/manual triggers
and the strict final gate. The actual documentation shell passed disposable push,
new-branch and PR fixtures and correctly rejected bad whitespace introduced earlier
in a multi-commit range. All18CI partition/acceptance safety checks passed again.
This change adds no package, runner service or mutable dependency environment.

The repaired first full run43073228929 passed at362b84e:2253backend checks with
11existing skips,58frontend,128browser with3existing skips, and12visual checks.
The final verify gate passed and produced the exact-commit full acceptance receipt.
Creation17:58:14 to final job completion18:06:52 is **8m38s**. This is the first
successful optimized sample; the next checkpoint measures another complete run
before reporting a median. The slower backend partition is now the critical path;
neither browser setup nor screenshots dominate. Private110remains unchanged.

## Accepted comparison

| Full successful run | Commit | Creation → final gate | Aggregate runner time |
| --- | --- | --- | --- |
| [43073228929](https://github.com/Twich-Team/TwichUI-Workbench/actions/runs/43073228929) | 362b84e | 8m38s | 36m56s |
| [43074816594](https://github.com/Twich-Team/TwichUI-Workbench/actions/runs/43074816594) | 9850c40 | 7m19s | 34m32s |
| Optimized median, two samples | | **7m59s** | **35m44s** |
| Baseline median, three samples | | **29m21s** | **29m18s** |

Wall time is workflow creation to the final job's completion, rather than GitHub's
later metadata update. Initial runner queue was3seconds in each optimized run;
dependent jobs generally started2–3seconds after creation. Final aggregation adds
8–11seconds. Both complete runs passed2253backend checks with11existing skips,
58frontend checks,128browser checks with3existing skips, and12visual checks.
The backend increase is18new CI safety checks; all original cases remain.

| Stage | Baseline median | Optimized median | Interpretation |
| --- | --- | --- | --- |
| Build/contracts/schema | 13s | 13s | Same checks and one transferred build |
| Backend command | 11m18s, including frontend | 5m54s, slowest backend partition | Frontend now runs separately in9s |
| Browser command | 15m50s | 4m24s, slowest browser shard | Four isolated serial workers |
| Full visual comparison | 46s | 46s | All12checks and original screenshots |
| Artifact upload | 10s, one job | 1–5s per job | Uploads overlap; same14-day evidence retention |

These stages overlap, so their savings must not be added. Overall median wait fell
by21m23s, approximately73%. Parallelism increases aggregate runner time by about
6m26s/22%; this is a latency improvement, not a claim of lower billed usage. No
larger/self-hosted runner, paid model call, billing setting or infrastructure service
was added. This small sample demonstrates the target, not a guaranteed service level.

Both pip and npm download caches hit on the accepted runs. Locked environment
creation and installation still run every time. Total setup was25s for checks,
30–53s for backend runners and44–85s for browser/visual runners, including their OS
packages. Browser binary caching is still not justified by those measurements.
The existing signed mirror protection remains. The earlier failed parallel run
is excluded from the successful median and its error/trace evidence is preserved.

Downloaded first-success backend artifacts independently prove an exact disjoint
127-file union,2264unique cases and2253passes/11skips. Their ZIP digests match the
GitHub artifact metadata. The full acceptance receipt matches the exact checkout
and inventory hash; the second full gate records the same inventory and its own
exact commit. Original browser collection partitions also cover all131cases once.
No new skip, weakened assertion, snapshot update or automatic test retry was added.

Backend fixture/setup and real controlled workflow tests now dominate the critical
path; the slowest recorded individual cases were ordinary-request orchestration
checks around12–13seconds. Keep their migrations, recovery checks and isolation.
The target is met without database-template caching, shared browser workers or a
timing-weighted scheduling layer. Further complexity is not justified now.

The checks job provides full build/frontend feedback in55–57seconds. The new
documentation workflow passed on the mixed checkpoint in6seconds of job time.
A final report-only push verifies its selection without launching another full
suite. That feedback never changes the full9850c40acceptance decision and cannot
authorize deployment of a different commit. Current private110and rollback109,
all saved history, credentials, model policy and live legacy scheduling stay intact.
