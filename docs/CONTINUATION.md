# One-shot preparation recovery ready for review — November 5, 2026

The offline engineering window added two local operator commands to the
existing preparation and worker owners. The retained 9187 job remains failed
with one original claim and zero recovery authorizations. Immutable read-only
revalidation passed the new eligibility gate, and all 118 retained hashes
matched. The applied scope and expectation approval
`f66f99d3-ace2-43e5-87ba-0cfd7b78fe83` is still valid for proposal
`1b47fb3c-7b00-55b5-b3a8-f326be4a38ac` revision 1 and clean fixture main
`ebb16fff02f755bf523f3a7c30787ebdc0c8e894`. The retained store is schema 0012
at device/inode `64512:6719454`. Its preparation policy, saved model selection,
committed evidence, reviewed scenario and canonical recipe matched.

## Recovery contract and verification

`wb recover-preparation` records one authorization for a named original job
and exact review binding. `wb run-preparation-recovery` starts the existing
worker in targeted mode under the zero-request guard. Both commands require
same-user access to the explicit existing SQLite file and private, store-bound
preparation policy. The worker opens the store and runs only that job's
preparation path. The regular worker ignores these targeted authorizations,
and the existing HTTP planning retry continues to refuse preparation jobs.

An eligible job has exactly one terminal failed claim, a retained hold and
recipe/oracle under its original private evidence directory, and current
scope-only approval. Existing offers, completion or certification markers,
missing retained inputs, or changed bindings block authorization or dispatch.
The worker reconstructs the exact reviewed recipe and committed grounding
evidence, checks the saved policy, and fences canonical offer registration to
the authorized review, recipe, worker claim and fresh evidence directory.

The journal stores a recovery ID, original job and attempt number, operator UID,
request time, binding hashes, claim and outcome beside the original failure in
the existing planning job. Concurrent requests serialize, and the committed
claim precedes creation of the new directory. A request saved before the claim
can be dispatched after restart. A dead claimed worker can reconcile only
complete independently certified evidence. A partial attempt or a second
failure holds, and dispatch of a terminal receipt starts no work. The original
failure, claim and evidence are preserved. A successful recovery registers one
offer for separate budget review and creates zero execution or publication
authority.

Ruff and 58 focused checks passed. Coverage includes concurrent requests and
claims, stale review/base/expectation/recipe/policy, access checks, active and
certified refusal, terminal second failure, and restart before and after offer
registration. The guarded worker also completed a disposable fixture using
actual delegated Runtime confinement. All four trusted suites passed with
private loopback, and the untrusted checks retained kernel network denial.
Every command cgroup was removed. The fixture produced one certified offer
and zero budgets, sessions, grants, implementation requests or publications.
The retained desktop and phone proposal screenshots were inspected. Current
browser acceptance and the original-store recovery remain separate.

Counted headroom was 77,519,935 bytes at entry and 77,514,297 bytes (73.92 MiB)
at closeout under the 512 MiB cap.
[Focused tests](../artifacts/runs/preparation-recovery.ytGSFs/tests.log),
[disposable confinement receipt](../artifacts/runs/preparation-recovery.ytGSFs/disposable-confined.json),
and [retained read-only audit](../artifacts/runs/preparation-recovery.ytGSFs/retained-read-only.json).

## Proposed original-store action after separate authorization

Use a separately authorized 60-minute window at this recovery checkpoint.
Before writing, repeat the immutable audit, confirm the original store and
fixture identities, and preserve the original SQLite bytes and closed hash
manifest. Require at least 72 MiB of counted headroom after preservation and
4 GiB free on the working filesystems. Verify the guarded confinement preflight
inside an owned delegated cgroup with the same worker environment. Use the
exact policy and fixture below. Stop on the first hold or timeout, or after
one certified offer. Verify the original evidence, the single retained planning
attempt, and zero run budgets, activations, sessions, grants, implementation
requests or publications. A changed binding requires fresh review.

Proposed commands for that future window, from the Workbench checkout.

```bash
set -eu
cd /home/rich/TwichUI-Workbench
run="$PWD/artifacts/runs/adapter-9187-20261105T221451Z-AqoKpk"
job=1da6e56b-3a67-466b-93a1-880f7f902052
binding=b002b6cf7d94c8dd9ab4a5d72be3742621ab01ee96b3653a0c2895589ed5a63e
export WORKBENCH_RESEARCH_ROOT="$run/fixture"
export WORKBENCH_PREPARATION_CONFIG="$run/preparation-policy.json"

python3 scripts/wb.py recover-preparation --database "$run/workbench.sqlite3" \
  --job-id "$job" --review-binding "$binding"

scratch=$(mktemp -d /tmp/wb-9187-recovery.XXXXXX)
unit="workbench-real-9187-recovery-${scratch##*.}"
systemd-run --user --wait --pipe --collect --unit="$unit" \
  --property=Delegate=yes --property=RuntimeMaxSec=45min \
  --property=MemoryMax=8G --property=TasksMax=256 --working-directory="$PWD" \
  /bin/bash -ceu '
    run=$1
    job=$2
    scratch=$3
    unit=$4
    IFS= read -r current < /proc/self/cgroup
    cg="/sys/fs/cgroup${current#*::}"
    test "${cg##*/}" = "$unit.service"
    test -O "$cg"
    mkdir "$cg/main"
    printf "%s\n" "$$" > "$cg/main/cgroup.procs"
    printf "%s\n" "+memory +pids +cpu" > "$cg/cgroup.subtree_control"
    export WORKBENCH_CG_ROOT="$cg" TMPDIR="$scratch"
    export WORKBENCH_RESEARCH_ROOT="$run/fixture"
    export WORKBENCH_PREPARATION_CONFIG="$run/preparation-policy.json"
    exec python3 scripts/wb.py run-preparation-recovery \
      --database "$run/workbench.sqlite3" --job-id "$job"
  ' bash "$run" "$job" "$scratch" "$unit"
```

## Previous offline transport guard checkpoint (49f30e5)

### Offline diagnosis and verification

The 45-minute window authorized diagnosis and controlled regression only.
Counted headroom was 73.95 MiB at entry and 73.93 MiB at handoff under the
512 MiB cap.
The real preparation stayed stopped. The original store was opened with SQLite
`mode=ro&immutable=1`, and all 118 retained hashes matched.

The guard lived in the [disposable acceptance wrapper](../artifacts/runs/adapter-9187-20261105T221451Z-AqoKpk/preflight-worker.py),
which installed a process-wide Python audit hook and transport wrappers before
entering the normal worker. Its `socket.connect` branch only admitted
`127.0.0.1:8080` against the HTTP request count. It rejected
`socket.create_connection(marker.getsockname(), timeout=1)` in
`real_confine.run`. The parent binds a temporary listener on IPv4 loopback,
connects, accepts, and keeps the listener open during the child check. That
proves the host marker is live before a trusted child tests that it cannot
reach the marker from its separate network namespace. The historical preflight
ran before the hook was installed.

The [offline operator guard](../scripts/acceptance_transport_guard.py) closes
all urllib requests and denies outbound socket connects except one TCP
connection to a listening IPv4 loopback socket that this process bound with
port zero after installation, on the same thread. Port 8080 is always denied.
The exception is consumed before the kernel connect. The guard also denies
audited datagram and message sends. This process-local Python guard governs
the trusted acceptance launcher. Untrusted commands still run under the
existing kernel confinement. The default check path denies socket creation.
Only the fixed trusted verification capability enters the private network
namespace with private loopback. It still has to pass the parent marker check
and the existing suite and certification gates.

The guard was installed before the actual `Runtime.check` rehearsal in an
owned delegated cgroup. A synthetic `bash tests/run.sh` suite passed the
parent self-connection and child checks for private loopback, host marker
denial and host IPC denial. A separate Lua-launched probe received kernel
`EACCES` for IPv4, IPv6 and Unix socket creation and could not reach the
disposable host listener or the test-net address. Both command cgroups were
removed and the transient unit was collected. The focused guard and profile
tests passed 39 checks. They covered an unrelated host listener, a second
marker connect, synthetic audit events for repeated model requests, and
the existing fixed checks and frozen evidence gates. The retained profile
still returns a hold and has zero offers, budgets, sessions, grants,
implementation requests and publication records.

[Runtime probe results](../artifacts/runs/offline-transport-9187-fDOEVT/runtime-results.json),
[focused tests](../artifacts/runs/offline-transport-9187-fDOEVT/focused-pytest.log),
and [read-only recovery audit](../artifacts/runs/offline-transport-9187-fDOEVT/recovery-audit.json).

## Previous 9187 normalization acceptance closeout

The owner-authorized window is closed as of 3:18:19 p.m. America/Denver.
Workbench ran at `e4d5a96b3e9880c2bfacd0d7fc093de9b7010b82` with pinned
synthetic fixture `ebb16fff02f755bf523f3a7c30787ebdc0c8e894`. The fresh schema
`0012` store at `64512:6719454` retains the one model attempt, applied scope
approval and failed certification job. The API served 22 assets hash-matched
to the checkpoint build after an isolated `wb check` pass.

Counted headroom was 56.42 MiB at entry. The existing evidence archive tool
preserved 28 closed browser files (19,364,784 bytes) under
`adapter-e4d5a96-20261105-closed-browser`. The archive was verified and
restored in isolation before the matching originals were removed. The
original-path lookup still resolves the closed failure trace. Both earlier
real attempts (57 and 61 hashes) and the verified installation/rollback
package (169 hashes) passed post-close verification.

The store-bound preparation policy enabled scenario generation. Saved model
policy revision 1 preferred and enabled `pilot-qwen38-27b` with unspecified
effort, substitution denied and no fallback. The delegated worker opened the
owner-designated credential read-only after verifying regular-file metadata.
Actual Runtime probes in that worker confirmed cgroup limits, Landlock read
denial, seccomp socket denial, private loopback, host marker and IPC denial,
descendant reaping and cgroup cleanup. The worker's capacity check before
transport found 77,944,505 counted bytes of headroom (74.33 MiB), more than
4 GiB free on each measured filesystem and 25,145,312 scratch bytes.

Chromium submitted **Cover lowercase name normalization** once. The description
contained the model-visible example below and named the only writable file.

```text
Example 1: {"args":[" ALICE "],"expected":"alice","incorrect":"ALICE"}
```

The worker invoked the checkpoint serializer against the actual saved intake
with an offline capture before the identity GET. It checked the exact example,
one committed module binding for the directly returned `normalize.lua`
function with one named parameter `s`, and the module's scalar-first-return,
empty-setup and empty-field-path restrictions. It required the subsequent POST
body to match those verified bytes. The existing adapter locks enclosed one
identity GET and one structured POST. The POST body was 12,368 bytes and the
response was 1,441 bytes. The exchange took 18.539 seconds and reported 1,683
prompt, 354 completion and 2,037 total tokens. The request specified 2,600
maximum output tokens, zero requested reasoning tokens and no tools.

The saved, advertised and response-envelope identities matched
`pilot-qwen38-27b`. The generated draft JSON separately labeled itself
`gpt-5`. This attribution discrepancy is retained in the original response,
parsed draft and attribution receipt. Workbench records the verified transport
identity. The draft passed the structured schema, exact bindings, committed
mapping and example checks. Its single scenario
`normalize-trimmed-lowercase` calls the function exported directly by
`!!!TwichUI/normalize.lua` with one ASCII space on each side of `ALICE`,
expects the first return `"alice"`, and requires rejection of `"ALICE"`.
Scope permits additive assertions only in `tests/test_normalize.lua`.
Both the 1440 px and 390 px intake and review screenshots were inspected.

The owner independently confirmed proposal
`1b47fb3c-7b00-55b5-b3a8-f326be4a38ac`, revision 1, after seeing the
discrepancy. The browser saved only the scope decision
`f66f99d3-ace2-43e5-87ba-0cfd7b78fe83`, and the worker applied it. The
existing offline preparation job ran once. Syntax and focused tests passed
for all four controls. The behavioral oracle passed the known-good addition
and failed the original, vacuous and wrong-behavior controls. The
wrong-behavior control accepted the injected `"ALICE"` outcome and the oracle
failed it with `OBSERVATION_NOT_ASSERTED`.

Certification held because the disposable transport audit hook denied the
trusted harness's parent-side self-connection to its own ephemeral loopback
marker. The four full-suite checks stopped before launching their sandboxed
children. The preliminary confinement probe passed before this guard was
installed, so it missed that interaction. The profile reported
`original: Incomplete or unsafe check evidence` and registered zero offers.
The scope decision and one failed preparation claim are retained. The
existing worker does not replay that failed job. Fresh authorization is
required for any further acceptance work. The first check in a new window
should exercise the trusted suite with the transport guard already active,
allowing the harness's owned marker while preserving the one-exchange limit.
The retained response and reviewed bindings are available for an offline
rehearsal under that authorization.

SQLite integrity and foreign-key checks passed. Counts were zero for run
budgets, activations, execution sessions, grants, execution requests and
publication. The owned API and worker stopped, the cgroup was removed and
9187 is free. The 9487 listener process and store identity matched at entry
and exit. The retained run has 118 verified evidence hashes. Final counted
headroom is **77,537,967 bytes (73.95 MiB)** under the unchanged 512 MiB limit.

[Closeout](../artifacts/runs/adapter-9187-20261105T221451Z-AqoKpk/RESULT.json),
[proposal](../artifacts/runs/adapter-9187-20261105T221451Z-AqoKpk/grounded-proposal.json),
[attribution receipt](../artifacts/runs/adapter-9187-20261105T221451Z-AqoKpk/model-evidence/attribution.json),
[certification hold](../artifacts/runs/adapter-9187-20261105T221451Z-AqoKpk/certification-hold.json)
and [archive index](../artifacts/archive-index/adapter-e4d5a96-20261105-closed-browser.json).

## Previous offline checkpoint and historical attempts

The earlier offline repair and two real 9187 attempts below remain closed.
Their examples with two leading spaces are historical. The latest owner
request and verified wire packet use exactly one leading ASCII space.

## Exact evidence from the second attempt

Reconstructed the 2,980-byte packet and 11,375-byte POST from checkpoint
`19b8605`, the immutable intake and the pinned Git fixture. Both matched the
wire receipt byte for byte by SHA-256. Replayed the exact saved response through
the old decoder, schema, three binding checks and fixture validator. It stopped
at the same committed-client-fixture error recorded by the worker.

The packet contained four complete committed blobs at `ebb16fff…`.

- `tests/run.sh` loaded the module for the Lua tests.
- `tests/test_normalize.lua` loaded `../!!!TwichUI/normalize.lua` if needed and
  asserted only that `normalize("alice") == "alice"`.
- `!!!TwichUI/normalize.lua` directly returned a function with one parameter
  `s`. The method calls on `s` and the test's literal established a string
  argument and a first-return comparison.
- `tests/harness.lua` did define `MakeClient` and `c.API.Normalize(value)`.
  The selected test never constructed that client. `tests/test_client.lua`
  was outside the packet and the selected scope.

The technical evidence was sufficient for the direct module binding. The
application had supplied raw blobs and the generic module/client schema, so
the model had to choose the fixture and operation. The schema defaulted an
omitted `args` to `[]`, allowed scenarios alongside questions, and admitted an
index of 1 even though the sole acceptance item occupies zero-based index 0.
The prompt asked for scalar arguments and grounded mappings. The response
omitted both argument arrays, paired an unsupported client binding with a
module scenario, and acknowledged the missing input only in a question.
The guard rejected the response before any scenario was accepted.

The intake asked to cover trimming and lowercasing and named `alice` as the
result. It supplied neither the exact string to pass nor the incorrect outcome
to reject. The operator review checklist held `"  ALICE "` and `"ALICE"`.
Neither was model-visible intent, and the response's `"Alice"` counterexample
had no basis in the intake. The committed implementation could show how a
chosen input behaves. Only the requester can choose the intended example.

## New planning boundary and handoff

Grounded preparation derives eligible per-test fixture bindings, operations,
calling conventions and named positional parameters from bounded committed Lua
loaders and explicit exports. The wire packet exposes those bindings and
the supported observation shape. The existing mapping validator still runs.
Dynamic or unrecognized mappings need technical clarification. The lexical
screen establishes eligible shapes for review and independent Lua checks.

Grounded generation requires exact user-supplied arguments, expected outcome
and counterexample. The requester can put one line per expected result in the
description or a clarification reply, using the one-based result number. A
later numbered reply replaces that example. Arguments are a JSON array,
including `[]` for a zero-argument call and `null` for an explicit Lua nil.
An optional `setup` array uses the existing client fixture grammar.
For this particular fixture, the operator's intended example would be

```text
Example 1: {"args":["  ALICE "],"expected":"alice","incorrect":"ALICE"}
```

The original intake now waits for that information before a planner attempt.
With complete values the response must be either scenarios with explicit
argument arrays or questions with zero scenarios. Submitted scenarios must
match the committed bindings and the user's concrete values and setup.
Independent expectation confirmation remains necessary before scope approval.
Only the existing offline Lua preparation and control path can certify a
reviewed offer. The answer box appears before the held scope details.

The clarified packet captured offline serialized to 12,318 bytes, below the
24,000-byte request limit. It contains one eligible module binding and the
user values above as controlled test data. An unchanged submission would
hold before transport. A separately authorized real attempt would be useful
after the owner supplies those exact values in the actual intake and fresh
preflight passes. Use the same one-exchange and certified-offer stop, with
the bound module target, explicit arguments and independent human review.
Controlled results establish neither real model behavior nor authorization.
The current 56.42 MiB of counted headroom is below the earlier 72 MiB
preflight floor.

[Exact reconstructed POST](../artifacts/runs/scenario-grounding-20261105-B5xLUl/reconstructed-19b8605-post.body)
and [offline serializer receipt](../artifacts/runs/scenario-grounding-20261105-B5xLUl/SERIALIZER.json).

The controlled wire replay covered omitted and empty arguments, unsupported
client selection, changed user values, mixed scenario/question output and a
clarification-only hold. Invalid drafts produced zero accepted scenarios,
scope decisions, preparation jobs, certificates, offers or execution authority.
The grounded normalization and two-argument numeric floor examples reached
certified offers after separate expectation confirmation and actual Lua
controls with simulated confinement telemetry. Chromium also exercised the
supported client field observation. The 1440 px and 390 px captures show the
clarification before scope details and exact values in the subsequent review.

[Checkpoint checks](../artifacts/runs/scenario-grounding-20261105-B5xLUl/CHECKPOINT.json),
[desktop clarification](../artifacts/runs/browser-VgB5yJ/screenshots/grounded-clarification-1440.png),
[phone clarification](../artifacts/runs/browser-VgB5yJ/screenshots/grounded-clarification-390.png),
[desktop review](../artifacts/runs/browser-r2Vnbs/automatic-scenarios/observation-1440.png)
and [enlarged phone review](../artifacts/runs/browser-r2Vnbs/automatic-scenarios/observation-390.png).

## 9187 real-adapter attempt closed at fixture mapping — November 5, 2026

The owner-authorized attempt used clean Workbench checkpoint
`19b86056bb3d1d4f637f2552ff565b554e2f35a4` and the pinned synthetic fixture
`ebb16fff02f755bf523f3a7c30787ebdc0c8e894`. The normal API and worker ran
on a fresh schema `0012` store at `64512:6719390`. The API served 22
hash-verified staged assets from unchanged frontend inputs. The private
preparation policy bound the store inode. Saved revision 1 enabled and
preferred `pilot-qwen38-27b` with unspecified effort, no fallback and
unavailable routing set to block. The owner-designated credential was a
regular `rich:rich` mode-0600 file, opened read-only in the worker before
the exchange. Evidence excludes the credential and authorization headers.

Actual Runtime checks in the delegated worker passed cgroup limit readback,
Landlock file denial, untrusted socket denial, trusted private loopback,
host marker and IPC denial, descendant reaping and cgroup removal. Counted
headroom was 77,464,579 bytes at entry and 77,063,595 bytes (73.49 MiB)
immediately before intake. The workspace, scratch and shared memory each had
more than 4 GiB free. Scratch measured 20,477,134 logical bytes at shutdown
under the 1 GiB ceiling. Both Chromium intake captures (1440 px and 390 px)
were inspected.

The browser submitted **Cover lowercase name normalization** once, with
additive assertions restricted to `tests/test_normalize.lua`. Under the
existing locks, LocalProvider made one identity GET and one structured POST.
Saved, advertised and returned API model identity matched
`pilot-qwen38-27b`. The POST was 11,375 bytes and the response was 2,325
bytes. The exchange took 25.906 seconds and reported 1,534 prompt, 577
completion and 2,111 total tokens. The request set 2,600 maximum output
tokens, zero requested reasoning tokens and no tools.

The structured schema and all three bindings passed. Workbench rejected the
draft at the committed-fixture mapping gate with the exact error
“Example normalize-client-trim-lowercase: the committed test/harness does not
establish a client fixture.” That scenario specified `fixture="client"`
and `target=["API","Normalize"]` for the module-only test. The other
scenario specified the directly returned module function. Both omitted
`args`, set `acceptance_index=1` for the sole result, expected `"alice"`
and supplied `"Alice"` as the incorrect outcome. The draft's unresolved
question mentioned `" Alice "` in prose. The scenario fields contain no
literal input to review. The documented intake wording described the behavior
without spelling out the exact intended input `"  ALICE "` or incorrect
outcome `"ALICE"`.

The held proposal has zero accepted scenarios. The attempt stopped before
human review, scope approval, offline certification controls and a certified
offer. Read-only SQLite checks found zero scope decisions, run budgets,
activations, execution sessions, grants, execution requests, preparation jobs,
offers and publication rows. There was one model attempt. The owner-authorized
attempt is closed and consumed.

Only the owned 9187 browser, API and worker stopped. The delegated cgroup
was removed and port 9187 was free. Port 9487 and its service and store
were left untouched. The supervisor removed owned scratch before final
manifest verification. The original store, source and protected evidence
remained in place and all 61 original manifest entries verified. An offline
check of the saved bytes reproduced the exact stored mapping error. Final
counted headroom after the validation receipt was **76,895,495 bytes
(73.33 MiB)** under the unchanged 512 MiB cap.

[Preflight](../artifacts/runs/adapter-9187-20261105T214139Z-Yimn2EdG/PREFLIGHT.json),
[wire receipt](../artifacts/runs/adapter-9187-20261105T214139Z-Yimn2EdG/wire.jsonl),
[closeout](../artifacts/runs/adapter-9187-20261105T214139Z-Yimn2EdG/RESULT.json),
[response body](../artifacts/runs/adapter-9187-20261105T214139Z-Yimn2EdG/model-evidence/response.body),
[parsed response](../artifacts/runs/adapter-9187-20261105T214139Z-Yimn2EdG/model-evidence/parsed-response.json)
and [validation receipt](../artifacts/runs/adapter-9187-20261105T214139Z-Yimn2EdG/model-evidence/validation-receipt.json).

## Grounded module-fixture contract checkpoint — November 5, 2026

Continue from the clean local commit containing this handoff. See the
[checkpoint receipt](../artifacts/runs/grounded-module-contract-20261105/CHECKPOINT.json)
for its SHA and checks. The previous execution-path checkpoint `fcf74ce` and
its evidence remain in `artifacts/runs/execution-path-20261105T200351Z`.

## Offline diagnosis and repair of the 9187 hold

Rebuilt the old POST offline using the retained immutable intake, committed
fixture, private policy and `fcf74ce` adapter. Both the 2,980-byte packet and
8,294-byte request matched the hashes in `wire.jsonl`. The request declared
one common scenario shape for both fixtures. It constrained the expected and
incorrect outcomes to scalars, but allowed up to eight setup steps and eight
observation path segments for either fixture. Neither the system instructions
nor the packet described the module-only restriction.

The matching advertised and returned model was `pilot-qwen38-27b`. The
retained SQLite job records `response_received` and the after-validation
error at `draft.scenarios.0`. That error establishes `fixture="module"`
with nonempty `setup`, nonempty `observe.path`, or both. Field validation had
already succeeded, including scalar `observe.expected` and `incorrect`. The
closeout explicitly says the 2,289-byte response body was not saved. The
exact field contents and which of the two conditions fired cannot be recovered
from the retained artifacts. The adapter decoded the response envelope and
`message.content` as JSON and passed that value directly to validation. The
worker's dump and model attribution assignment occur after validation and
could not have introduced this mismatch.

The grounded response schema now distinguishes modules and clients by
`fixture`. It gives modules exactly one source and omitted or empty `setup`
and `observe.path`, and requires a nonempty client target. The grounded system
prompt explains direct and exported function targets, the scalar first return,
client field observations and when to return an unresolved question. The
shared stored scenario, compiler, safety validators, mapping checks and review
and certification gates retain their existing behavior. An unsupported response
holds with a field-specific reason.

The original synthetic request fits the existing fixture. The committed test
loads `../!!!TwichUI/normalize.lua`, which directly returns a function that
trims and lowercases its scalar input. The example calls that function with
`"  ALICE "`, expects the first return `"alice"` and rejects `"ALICE"`.
This needs neither setup nor a field path. The existing test covers only the
already-normalized input `"alice"`.

Offline verification covered both synthetic triggers for the retained error,
a table-valued expectation, multiple module sources, an empty client target
and the supported counterpart. The controlled local transport exercised JSON
decoding and validation on disposable stores. Invalid drafts held with zero
accepted scenarios, rejected approval and created no decision, preparation
job, certificate, offer, budget, execution session, grant, request or
publication. The valid counterpart passed the existing durable review and Lua
control path to a certified offer with controlled confinement telemetry and
no execution authority. The local llama.cpp schema converter and GBNF validator
also accepted the supported module and client samples and rejected the new
structural violations. The revised POST measured 11,375 bytes with the retained
packet, within the 24,000-byte limit. The focused backend run passed 91 tests
and `wb check` passed. This was a backend-only repair with no browser rerun.

Entry headroom was 77,465,659 bytes (73.88 MiB) under the unchanged 512 MiB
limit. All 57 retained evidence hashes verified. No inference or live service
work occurred in this repair. A separately authorized real attempt on the new
checkpoint must still produce a valid proposal and pass independent human
review and certification before real-adapter acceptance.

## Fresh 9187 adapter attempt held at scenario validation — November 5, 2026

The owner authorized one fresh 60-minute attempt. The disposable API and
worker used checkpoint `fcf74ce75262e07ee9f59fd55fac9a0a10b22a3d`, 22 verified
frontend assets, a new schema `0012` SQLite store at `64512:6719311`, and
clean synthetic fixture `ebb16fff02f755bf523f3a7c30787ebdc0c8e894`. The
private preparation policy matched the store inode. Saved revision 1 enabled
and preferred `pilot-qwen38-27b`, with unspecified effort and no fallback.
The owner-designated regular credential file was `rich:rich` and mode
`0600`. The delegated worker opened it read-only for the readability check.
Only the existing LocalProvider read its contents via `WORKBENCH_LOCAL_KEY`.

Fresh probes through the actual Runtime in the worker process passed cgroup,
Landlock, seccomp, private-loopback and host IPC isolation checks, plus
descendant reaping and cgroup removal. Entry counted headroom was 74.35 MiB.
Headroom immediately before intake was 73.97 MiB, and the workspace, scratch
and shared-memory filesystems each exceeded 4 GiB free. Owned scratch measured
20,437,675 bytes at closeout under the 1 GiB ceiling.

Chromium submitted **Cover lowercase name normalization** once, limited to
additive assertions in `tests/test_normalize.lua`. The existing LocalProvider
sent one identity GET and one structured POST using the existing locks. The
advertised and returned model both matched `pilot-qwen38-27b`. The POST body
was 8,294 bytes, the response was 2,289 bytes, and the exchange took 27.003
seconds. The request specified 2,600 maximum output tokens, zero requested
reasoning tokens and no tools. The provider reported 579 completion tokens.
The wire receipt retains the packet, input, evidence and policy digests.

Workbench rejected `draft.scenarios.0` with “Module scenarios support a
scalar first return only.” That validator rejects a module scenario with
setup steps or a nonempty observation path. The retained store has a held
proposal record with zero accepted scenarios. The requested expectation that
`"  ALICE "` returns `"alice"` and rejects `"ALICE"` therefore never
reached human review. There was no retry, scope approval, control preparation
or certified offer. Final SQLite checks found zero budgets, activations,
execution sessions, grants, requests and publication rows.

The owned browser, API and worker stopped, the delegated cgroup was removed,
and port 9187 was free. The store, policy, fixture, logs and partial evidence
were retained and 57 file hashes verified. The desktop intake capture was
inspected. Closeout preceded phone and review captures. Port 9487 and its
store were untouched. Final counted headroom was **77,465,659 bytes
(73.88 MiB)**. This attempt is closed.

[Preflight receipt](../artifacts/runs/adapter-9187-20261105T212620Z-KDcdGhku/PREFLIGHT.json),
[wire receipt](../artifacts/runs/adapter-9187-20261105T212620Z-KDcdGhku/wire.jsonl)
and [closeout receipt](../artifacts/runs/adapter-9187-20261105T212620Z-KDcdGhku/RESULT.json).

## Review installation complete — November 5, 2026

**Installed** `fcf74ce75262e07ee9f59fd55fac9a0a10b22a3d` on
`127.0.0.1:9487` at 20:33:32 UTC. The existing rich-owned launcher restarted
the supervisor, API and worker as PIDs `2320108`, `2320109`, and `2320110`.
The API serves the checkpoint OpenAPI schema and the index references
`index-CkG6v3Ki.js` and `index-CuekSOBd.css`. The clean detached checkpoint
passed `wb check`. Its build matched both retained checkpoint builds byte for byte. The
shared launcher checkout matched that source apart from the two existing
handoff edits. The planner remained in demo mode with model execution inactive.

The isolated rollback rehearsal passed before cutover on `127.0.0.1:9387`.
The private backup at `.local/m5-review/backup-fcf74ce-uug9d2ee` verified all
169 SHA-256 entries. It includes the prior backend and served assets, saved
environment and launcher, Python environment, SQLite snapshot, retained
one-off configuration, template and proof, the full `fixtures/` tree, and
the product and ownership documents read by the planner. The prior build
started from the packaged code and Python environment with a read-only
SQLite copy, no worker, and outbound connections blocked. It passed the
documented GET checks including `/api/work`, and the listener shut down.
The packaging and cutover helper is retained in
`.local/m5-review/install-fcf74ce-rehearsed-b5R7444B/install.py`.

The installed API and worker opened the intended live store
`.local/m5-review/review.sqlite3` at device/inode `64512:5935325`. Its schema
is `0012`. Integrity and foreign-key checks passed, and the schema and all
78 application rows matched the backup and rehearsal. The queue had zero
runnable work before and after restart.

GET-only acceptance passed for Work, Models and observed activity, History,
the saved guild intake, execution and delivery, and the served assets.
Chromium rendered Work, Models and the checked guild result at 1440 and
390 px with no browser errors. The saved draft link was checked locally.
Screenshots from both builds were inspected. No rollback, migration,
unexpected activity, model call, task execution or publication occurred.
This records installation and read-only acceptance. The separate 9187
adapter acceptance is tracked below. Final counted evidence headroom
was **25,975,137 bytes (24.77 MiB)** under the 512 MiB cap.

[Installation receipt](../artifacts/runs/review-fcf74ce-20261105T202622Z/RESULT.json)
and [preflight receipt](../artifacts/runs/review-fcf74ce-20261105T202622Z/PREFLIGHT.json).

## 9187 adapter acceptance held before authentication — November 5, 2026

The owner authorized one conditional 60-minute window. On the exact
`fcf74ce75262e07ee9f59fd55fac9a0a10b22a3d` checkpoint with 22 hash-verified
frontend assets, the normal API listened at `127.0.0.1:9187` and the normal
worker used a fresh schema `0012` store at device/inode `64512:6692914`.
The private preparation policy was verified against that inode. The synthetic
fixture was restored at clean `main` `ebb16fff02f755bf523f3a7c30787ebdc0c8e894`.
The saved and read-back model policy revision 1 enabled and preferred
`pilot-qwen38-27b`, with unspecified effort, substitution denied, empty fallback
and unavailable routing set to block. The copied source had no execution
runtime capability, templates or publication configuration.

Both actual `Runtime.check` confinement probes passed in the same delegated
process that became the normal worker. Cgroup readback showed 6 GiB memory,
zero swap and 200% CPU, with 64 tasks and 16 MiB per file for the untrusted
probe and 128 tasks and 128 MiB per file for the trusted probe. Both denied
the forbidden file read. Untrusted socket creation was denied. The trusted
probe used a distinct network namespace with working private loopback, and
could reach neither the parent marker nor host IPC. Both probe cgroups were
removed after reaping descendants. Entry counted headroom was 74.99 MiB.
Workspace, `/tmp`, and `/dev/shm` each had more than 4 GiB physically free.
Owned scratch measured 20,437,675 logical bytes at closeout under the 1 GiB
ceiling. The 1440 px and 390 px Chromium captures show the filled, unsubmitted
synthetic intake. Both images were inspected.

The handoff lacked an owner-designated 8080 key path. The path was requested
and no designation arrived during the run. Intake remained empty. There were
zero identity GETs and zero generation POSTs, so returned identity and usage
were unobserved. Offline serialization with the actual store policy and a
placeholder request ID verified an 8,294-byte structured POST, 120-second
exchange configuration, 24,000-byte body ceilings, a 2,600-token output
setting, zero requested reasoning tokens and no tools. The first offline
serialization helper stopped before building a body because it lacked the
transport deadline attribute. The corrected helper ran entirely offline.

There was no generated proposal, human review, control preparation or
certified offer. Final read-only SQLite checks found zero run budgets,
execution sessions, grants, execution requests and publication rows. The owned
API, worker and browser stopped and the delegated cgroup was removed. The
disposable store, policy, fixture, logs, screenshots and probe evidence were
retained. The 9487 service and store and the 8080 service were left untouched.
Final counted headroom was **77,963,150 bytes (74.35 MiB)**. The hold closed
this run. A later attempt requires the exact owner-designated existing key
path, a newly authorized window and fresh preflight.

[Result and boundary counts](../artifacts/runs/adapter-9187-20261105T2052Z-krFBiM/RESULT.json)
and [retained evidence hashes](../artifacts/runs/adapter-9187-20261105T2052Z-krFBiM/SHA256.json).

## Previous 9187 acceptance readiness — November 5, 2026

**Held before inference.** The original 60-minute proposal below is ready for an
explicit, conditional authorization. Actual confinement must first pass in the
new worker's delegated cgroup. This proposal's disposable worker and store
still need to be created, along with the saved policy and store-bound file.
The owner needs to designate the existing private 8080 key file. Authentication
and the endpoint's advertised identity can only be confirmed by the authorized
identity GET after offline readiness passes. The installed process arguments
show `pilot-qwen38-27b` and the Qwen3.8-27B GGUF on loopback 8080. Socket and
process inspection found 9187 free and the 9487 API and worker still using
`.local/m5-review/review.sqlite3`, device/inode `64512:5935325`, with the demo
planner. The readiness session made no HTTP calls.

Counted headroom rose from **25,975,137 bytes (24.77 MiB)** to
**78,627,986 bytes (74.99 MiB)** under the unchanged 512 MiB cap, a net recovery
of **52,652,849 bytes (50.21 MiB)** including the new receipts and fixture bundle.
The archive contains 52,662,879 bytes in 90 files across 11 closed Playwright
output trees. Same-user ownership and process and file references were checked.
Three roots have diagnostic references in retained browser logs. Before pruning,
an isolated restore matched all original paths, hashes, modes, and modification
times for 90 files and 24 directories. Every file resolved by original-path lookup.
The archive verified again after pruning. Primary inspection images, the retained
candidate, approval and database records, source recovery, active runtime roots,
and both installation packages stayed in place. All 169 corrected rollback
manifest hashes and 149 earlier package hashes were rechecked. Physical free
space on the workspace, `/tmp`, and `/dev/shm` exceeded 4 GiB.

Use archive ID `adapter-readiness-20261105-browser-generated` with
`scripts/evidence_archive.py lookup` on the original file path, or `restore`
with an isolated destination. The [archive index](../artifacts/archive-index/adapter-readiness-20261105-browser-generated.json)
pins the external manifest. The [selection receipt](../artifacts/runs/adapter-readiness-20261105T203747Z/ARCHIVE_OWNERSHIP.json)
and [restore receipt](../artifacts/runs/adapter-readiness-20261105T203747Z/archive-restore.json)
record the checks.

The existing controlled desktop capture confirms the exact module fixture base
`ebb16fff02f755bf523f3a7c30787ebdc0c8e894`, clean `main`, tree
`7720d09d721cf4fb524faf338586d97a20fed6bb`. The enlarged phone capture exercises
the sibling client fixture. Both were inspected from retained evidence. The
test has a literal loader of `!!!TwichUI/normalize.lua` and calls its returned
function. Read-only Git inspection and the lexical mapping check passed for the
specified input and its counterexample. With placeholder request and store
identities, the exact request yielded a 2,980-byte packet and an 8,294-byte POST
body, including the schema. The final request and policy bindings must be computed
with the new identities. A source-only bundle of that same fixture was restored
and checked offline so the exact base survives temporary-directory cleanup.
[Fixture bundle](../artifacts/runs/adapter-readiness-20261105T203747Z/normalization-fixture-main.bundle)
and [fixture and packet receipt](../artifacts/runs/adapter-readiness-20261105T203747Z/fixture-proof.json).

## Previous installation attempt — November 5, 2026

**Stopped before cutover** during the separately authorized 30-minute installation
of `fcf74ce75262e07ee9f59fd55fac9a0a10b22a3d` on `127.0.0.1:9487`.
The checkout was clean at entry. The fresh staged build produced
`index-CkG6v3Ki.js` and `index-CuekSOBd.css`, matching the checkpoint build.
At that stop, the original `tested-release-final-task-pr-completion-18jzz1vj` build served
`index-ns7R_4Mn.js` and `index-CuekSOBd.css` under the original supervisor, API
and worker (UID `1000`, PIDs `2032832`, `2032833`, `2032834`).

The private backup is `.local/m5-review/backup-fcf74ce-mq30ybb0`. All 149
manifest hashes verified. The live SQLite store, backup and isolated copy passed
integrity and foreign-key checks and matched across the schema and all 78
application rows. The service used `.local/m5-review/review.sqlite3`, device
`64512`, inode `5935325`, schema `0012`. The isolated rollback rehearsal stopped
at GET `/api/work` because the copied release tree lacked `fixtures/catalog.json`.
The original checkout has the file. The rollback prerequisite failed, so the
original processes, served assets and saved environment stayed in place.

Before the stop, GET checks on the original build returned HTTP 200 for Work,
Models and observed activity, History, and the existing guild intake, execution
and delivery. Chromium loaded Work, Models and the guild result at 1440 px and
390 px. The historical draft link was verified locally. There was no cutover,
installation acceptance, queued work change, migration, task start, inference,
execution or publication. Entry headroom was 27,305,168 bytes (26.04 MiB), with
more than 4 GiB free on disk. The [stop receipt](../artifacts/runs/review-fcf74ce-20261105T201708Z/RESULT.json)
records the final measurement.

## Execution path and evidence

| Transition | Existing owner | What the evidence establishes |
| --- | --- | --- |
| Browser submission → durable request | `Intake.tsx` → `POST /api/intake` → `intake` writes the immutable original and planning job | Actual Chromium, API and SQLite on disposable stores. |
| Worker → selected local model → grounded proposal | `worker.tick` → `planning_worker` / `regression_preparation` → `model_policy.execution_policies` selection and `planning_provider.LocalProvider` / `planning_transport.LocalHTTP` | The fresh disposable run reached real inference with matching saved, advertised and returned model identity. The response failed structured scenario validation before grounding or human review. Controlled tests passed the proposal and decomposition path. |
| Proposal → human scope/expectation review | `grounded_scenarios` binds committed Git evidence, original input and feedback to the typed contract. `intake` / `planning_worker.apply_one` own the exact proposal and independent expectation confirmation | Actual browser confirmation, SQLite receipt and task materialization on both supported fixture shapes. Model output was controlled. |
| Applied review → certified offer | Single-task decomposition journals `behavioral_preparation` in `planning_jobs`. `trusted_preparation` uses `task_session.provision`, `Runtime`, `verification_profiles` and `task_budget.register_reviewed_offer` | Actual durable claims, Lua controls and canonical offer registration. Current grounded browser tests substitute confinement telemetry. |
| Offer → separate authorization | `RunApproval.tsx` → task `run-approval` API → `task_budget.approve_run` and the existing decision worker | Actual browser/API and distinct durable receipts in disposable stores. Certification alone creates no session, grant or request. Normal one-off admission requires the later applied `activate_once` decision, matching store capability and an unexpired offer/budget. |
| Authorization → provision → execution | `task_sessions` journals one-off ownership. `task_session` uses the existing delegated `provision_task.py` child and `chronicle_lease.py` controller. `real_execution` owns the grant, request, fence and attempt ledger. `Runtime` and `real_provider` own tools, model calls and checks | Ordinary browser ingress through this transition has controlled executor evidence. A supervised owner-ingress Chronicle run previously exercised the real lease, probe, model, execution and restoration. |
| Candidate → independent verification → durable result | `Runtime`, `real_confine`, `real_sandbox`, `verification_profiles`, `real_handoff` and `real_execution` own confinement, exact scope, fixed checks, freeze/seal and SQLite recovery | Historical real Chronicle and assisted guild candidates exercised those owners. The new ordinary-request route has yet to run implementation or candidate verification. |
| Verified result → draft PR | `task_session` waits for verified restoration. `real_publication` owns delivery state and the sealed result/current budget/usage checks. `PilotGitHub` stages the exact local commit, reconciles the branch and creates/confirms an open draft | Historical guild PR #6 exercised the real remote path using explicitly authorized Codex completion after three failed local attempts. Site session delivery has controlled evidence. Local Git staging and recovery also passed without network. |

## Change and remaining audit

The first adapter integration gap was model selection. The grounded worker used
whichever model the endpoint advertised and left the saved policy revision empty.
For real local grounded planning, the worker now reads the existing saved policy
inside the durable attempt claim. It requires an enabled preferred local model
and unspecified effort. The existing transport must observe the exact identifier
before generation, and the returned model must match it. The attempt keeps the
requested model and revision even on failure. The validated proposal keeps the
selection in its review-bound grounding. A preference save affects new claims.
Historical contracts omit the new optional field. Generic planning keeps its
existing adapter selection. Independent expectation review, preparation and the
execution recipe's fixed model and budget remain distinct.

- **Routing and capacity.** This binds one route and its attribution. Live
  capability/availability and capacity observations for `model_policy.evaluate`,
  account allowances, fallback and implementation-policy acknowledgment remain
  open. The fixed recipe uses `pilot-qwen38-27b`; it consumes the human-approved
  offer, including the recipe's limits, through the session and grant.
- **Real task and confinement.** On retained TwichUI base
  `f48b2e0bbdf966293695de8b1435b0e7e4372224`, the existing loader screen found no
  supported literal addon loader in 164 regular Lua tests plus the harness. Seven
  small representative paths also exceeded current grounded-context bounds.
  `tests/test_map_viewmath_tileat.lua` uses `loadfile(ROOT .. ...)` and fails these
  gates. This is local evidence about that base. No current production case was
  established. Pre-offer preparation calls the provisioner in the application
  worker and needs a dedicated delegated cgroup supplied by its owner. The post-budget
  controller already provisions in a delegated child. Actual pre-offer lifecycle,
  cancellation and cleanup on the installed worker remain unproved.
- **Recovery, retries and budgets.** Preparation claims reconcile complete evidence
  and hold partial output. Interrupted generation records unknown completion and
  has no automatic replay. The controller has finite leases and a one-attempt
  grant; bounded correction inside an attempt is already supported. Broader
  retries/fallback remain open. Planning (2,600 requested output tokens), the
  cancellation probe (60) and implementation (24,000) have separate ceilings.
  There is no shared planning-to-delivery reservation or unattended process that
  enforces the aggregate 512 MiB evidence cap across all stages.
- **Delivery and unattended operation.** The canonical delivery row journals write
  intents and reconciles uncertain outcomes with bounded reads. Historical real
  staging failed initially and was recovered on the same candidate and row, while
  its original private session marker remained held. Ordinary browser-to-real-draft
  delivery and unattended lifecycle consistency remain unproved. The browser run
  offer currently sends `publication="draft-pr"`. The same run-approval API
  accepts `publication="hold"` for a verification-only run.
- **Browser acceptance.** This session passed 186 focused backend tests, `wb check`
  and two targeted Chromium scenarios at 1440px and 390px. Inspected the desktop
  review, enlarged phone observation and phone run offer in `browser-ZKHwKe`.
  The historical 64/92 run and 28 uncovered cases remain separate, along with
  physical Safari and full current verification.

## Proposed separate authorization

**Separate first real-adapter acceptance, 60 minutes.** Run the repaired checkpoint
with the normal API and worker on a fresh disposable store at `127.0.0.1:9187`.
The exact browser task is **Cover lowercase name normalization** in the existing
synthetic `tests/test_normalize.lua` fixture from `controlled_source` in
`test_grounded_workflow.py`. The expected scalar return for `"  ALICE "` is
`"alice"`. The control must reject `"ALICE"`. Label the repository and request
as fixtures. Pin its clean main SHA and packet, bind the private preparation file
to the disposable DB inode, and demonstrate actual owned cgroup/Landlock/seccomp
and private-loopback readiness before permitting any inference. Require 72 MiB
counted headroom (64 MiB preparation ceiling plus 8 MiB checks/handoff), 4 GiB
physical free space and 1 GiB maximum scratch. Remeasure capacity before setup.

Authorize `WORKBENCH_PLANNER=local` and `scenario_generation=true` only on the
disposable worker path. Permit exactly one local request using saved enabled preferred
`pilot-qwen38-27b` (Qwen3.8-27B), unspecified effort, fixed zero reasoning budget,
existing key/locks, one identity GET and one structured POST to
`127.0.0.1:8080`. Bound the exchange to 120 seconds, 24,000 bytes each way and
2,600 requested output tokens. After independent human review, permit only the
existing offline controls under the 40-minute Runtime clock, 6 GiB RAM, no swap,
200% CPU and 64 tasks per untrusted check (128 for trusted suite verification).
Stop at the certified offer and verify zero execution/delivery authority. Stop
earlier on lock/model mismatch, unknown completion, invalid mapping, failed
review/control, loss of confinement or exhausted limits. No retry, fallback,
lease, cancellation probe, implementation or remote write. Stop only owned
disposable processes and retain the store, receipt and outputs. The existing
8080 and 9487 configurations need no change for this stage.

### Authorization checklist for the 9187 proposal

1. **Authorize isolated setup for this one 60-minute window.** Use the exact
   repaired Workbench SHA in the checkpoint receipt and matching built assets in
   a private copy. Initialize schema `0012` only on a new disposable SQLite file.
   Start the normal `workbench.api:app` on `127.0.0.1:9187` and prepare one normal
   `workbench.worker` launch with an explicit clean environment. Keep intake empty
   through the offline readiness checks. The disposable dev wrapper forces demo
   mode and its E2E variant injects controlled behavior. Check the new
   DB device/inode, empty queues, absent runtime capability for this store, and
   disabled execution, templates and publication. Supply no live DB, pilot
   projection, resource/policy DB, runtime or delivery roots. Use separate owned
   API, worker, browser and scratch paths. Keep the retained evidence and DB in
   a private counted run root. Confirm 9187 is free, recheck 72 MiB counted
   headroom and 4 GiB free space before starting, and enforce the 64 MiB preparation,
   8 MiB checks/handoff and 1 GiB scratch ceilings throughout.
2. **Pin the fixture and saved policy.** Restore the bundle's exact clean
   `main` into a new research root. Keep `Twich-Team/TwichUI` as the repository
   label the schema expects and set the recognized origin URL as local metadata
   only. Use local Git with hooks and remote transport disabled. The Runtime
   verification clone removes its origin. Create a mode-0600 preparation file
   with this DB's absolute path and device/inode, the owned output root, a fixture
   name/email and only `lua-regression-tests` allowed. Set
   `scenario_generation=true` for this isolated workflow. The disposable API
   needs the same research/config paths to revalidate the offer. Only the worker
   needs `WORKBENCH_PLANNER=local`, the 120-second deadline and the owner-designated
   existing private key path. Save and read back revision 1 on 9187
   with preferred `local`, enabled `pilot-qwen38-27b`, unspecified effort,
   substitution denied, an empty fallback list and unavailable routing set to
   block. Keep the policy and source fixed.
3. **Authorize actual offline confinement checks before the one request.** Place
   the disposable worker in a dedicated user-owned `workbench-real-*` transient
   cgroup with `Delegate=yes`. As in the existing provisioner, move the launcher
   into its own child before enabling memory, pids and CPU controllers and
   setting `WORKBENCH_CG_ROOT` inside that tree. Read back 6 GiB memory, zero
   swap, 200% CPU, 64 tasks and 16 MiB per file for untrusted checks, and 128
   tasks and 128 MiB per file for trusted suite checks. Through the actual
   `Runtime.check`/`real_confine` launch path, prove forbidden file reads and
   socket creation fail for the untrusted mode. For trusted verification, prove
   a distinct network namespace, working private loopback and failed access to
   a disposable parent marker and host IPC. Require safe descendant reaping and
   cgroup removal in both modes. Reuse the boundary assertions in
   `task_session.provision` and `backend/tests/test_real_network.py`. An installed
   binary, static kernel capability, prior delegated unit or substituted telemetry
   cannot prove this worker is confined. Keep these checks offline and local to
   disposable paths, and start the normal worker in the proven tree before
   submitting intake.
4. **Conditionally authorize one browser submission and one planning exchange.**
   Submit nonvisual feature **Cover lowercase name normalization**. Use
   description “Synthetic fixture only. In tests/test_normalize.lua cover
   trimming spaces and lowercasing names.” Select regression preparation and only
   `tests/test_normalize.lua`. Use “Spaces and capital letters normalize to alice.”
   as the sole expected result. Record the actual base and packet/input/evidence/
   preparation-policy hashes. Verify the saved model policy revision and the
   serialized request size. The adapter may take the two existing locks and use
   the private key for one `GET /v1/models` and at most one structured
   `POST /v1/chat/completions` on 8080. Keep 120 seconds total for the exchange,
   24,000 body bytes each way, zero requested reasoning budget, no tools and
   `max_tokens=2600`. Require the expected identity before the POST and the same
   identity in the response. The current sampler source and historical limit
   evidence support the wire setting. Verify reported completion usage at or
   below 2,600 before approving scope. Hold if the response leaves that limit
   unverified. The 120-second timer closes the client socket. Server-side
   completion after timeout can remain unknown.
5. **Require independent human scope and expectation review.** The owner or an
   independent reviewer able to inspect the fixture checks the exact proposal
   revision and bindings. Confirm the returned module function, scalar input
   `"  ALICE "`, first result `"alice"` and meaningful incorrect result
   `"ALICE"`. Confirm only additive assertions to the existing test file, with
   production code, runner and harness excluded. Use the separate expectation
   confirmation and ordinary **Approve proposal** action. Leave any run-budget
   option unselected. The applied receipt binds the expectation digest, and
   the worker materializes one task and prepares the original, positive,
   vacuous and wrong-behavior controls. Require the exact existing suite
   inventory, actual confinement and independent certification within the
   40-minute Runtime clock. Stop at the certified offer. Verify the store has
   exactly one grounded planning attempt and one offer on success, with no run
   budget, `activate_once`, execution session, grant, execution request or
   publication row.
6. **Stop on any gate failure or when the window ends.** Hold on a timeout,
   unknown completion, lock contention, authentication error, model mismatch,
   changed or denied policy, invalid response or unsupported mapping, missing or
   declined human confirmation, failed control or confinement, insufficient
   capacity, or exhausted time or output limits. An incomplete or over-limit
   response cannot pass. Keep the terminal reason and any observed usage.
   Stop only owned disposable browser, API, worker and cgroup descendants.
   Retain the DB with its original inode, the private config and pinned source
   needed to reconstruct the offer, model/attempt attribution and timing,
   approval and certification receipts, frozen controls, logs, hashes and
   desktop/phone screenshots, or the partial evidence on failure. Clean up only
   owned transient scratch after verifying the retained files. A timeout does
   not authorize a retry, a lease, a cancellation probe or a service restart.

The proposal above was the basis for the closed `19b8605` attempt recorded
at the top of this handoff. Any future real attempt requires fresh owner
authorization and preflight.

### Separate real-repository implementation proposal

A real-repository implementation demonstration stays held until a clean current
TwichUI base and exact task pass the existing packet and mapping gates. It would
also require the reviewed offer, explicit execution consent, current installed
sampler/cancellation proof and verified 8080 restoration, with one authorized
lease/probe/attempt. The existing run budget is 90 minutes total, 20 turns, 24,000
generated tokens, 2,500 per response, 20 minutes implementation and 40 minutes
through verification. It admits no fallback. A verification-only test can use
the existing API with `publication="hold"`, exercise local candidate and
delivery preview/staging, and test remote-state transitions with a controlled
publisher. Actual delivery needs distinct authorization for the exact current
candidate/base, one non-force maintenance-branch push and one open draft to
`Twich-Team/TwichUI:main` within the original 15-minute publication window,
verified restoration and usage, and private delivery activation. The existing
first-budget rule gives a verification-only decision no later upgrade path.

## Preservation

The execution-path checkpoint session performed no installation, model inference,
lease, probe, migration or external write. Its entry counted 508,243,398 bytes with 28,627,514 bytes (27.30 MiB) free
under the unchanged 536,870,912-byte cap. The targeted checks fit without archival.
Final headroom is recorded with the checkpoint.
