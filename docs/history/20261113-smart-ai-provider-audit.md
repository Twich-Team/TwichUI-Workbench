# Smart AI Choices: provider audit — November 13, 2026

Following the Project Working Philosophy: establish actual access and limitations
before changing production routing. The owner promoted Smart AI Choices and Claude
integration after Milestone 3, with checkpoints A–D and the stated exclusions.
This audit is evidence for checkpoint A, **not a completed adapter or release**.

## Installed implementation

The running application is 50cc64a. All 441 tracked files inspected under backend,
frontend/src, scripts and migrations match that installed release byte for byte.
The live Models, local-work and model-activity endpoints responded successfully.
Installed policy revision 73 prefers pilot-qwen38-27b, with unspecified effort;
Codex is enabled with gpt-6.1-terra / medium, task fallback is Off, and Claude is
disabled and unconfigured. These values were not changed to the assignment's
illustrative GPT-6.1 Sol / High strategy.

| Existing owner | Preserve and extend |
| --- | --- |
| model_policy.py / execution_policies | Append-only global model, effort, provider and fallback preferences; revision conflict checks; pause preservation. Its generic evaluate function is a preview, not the production dispatcher. |
| provider_routes.py / TaskAISettings | Actual four local/Codex task routes, validated model choices, saved permission receipts and frozen task overrides. Claude cannot currently be selected for execution. |
| regression_preparation.py / feature_planning.py | Actual planning and follow-up dispatch, model-attempt attribution and narrowly permitted pre-generation fallback. |
| task execution / codex_runtime.py | Separate approved execution scope, confinement, independently checked edits and durable recovery. Planning choice is not run permission. |
| provider_receipts.py / existing native journals | Settlement interpretation independent of content acceptance, unknown usage and consequential-effect holds. |
| model_accounting.py / usage_view.py / Usage UI | Observed activity and token history/charts, distinct from subscription allowance. |
| scripts/codex_connector.py / codex_provider.py | Included Codex subscription checks, credit checks, model/effort validation, isolated tool-free dispatch, interrupt and native terminal evidence. |
| scripts/discover_models.py | Claude currently has version/help metadata only; no operative Claude adapter or verified model catalog. |

The existing policy payload and task journals are the extension points. An ordered
strategy must be frozen in the existing authorization receipt and consumed at the
real dispatch boundaries, including follow-ups and implementation. A UI preview or
the old bounded three-provider evaluator does not establish production routing.
No database migration or parallel scheduler is justified by this audit.

## Claude installation and subscription

At first inspection, /home/rich/.local/bin/claude resolved to the native 2.1.316
binary. Starting the native UI allowed its already-configured automatic updater
to advance the link to **2.1.323**. Subsequent checks used the exact new binary,
with the updater disabled for each subprocess. Neither a manual update nor any
model download was requested. Retain both versions and their separate evidence.

Current executable: /home/rich/.local/share/claude/versions/2.1.323.
SHA256: 604d29a6869409106b6a1110bdadcc8b72e8654a5a865d23258f938a696e348e.
Native auth status reports loggedIn=true, authMethod=claude.ai,
apiProvider=firstParty, subscriptionType=pro. No credential value was printed,
copied into Workbench or sent to another service. No billing-related environment
variables were present in the inspected login environment.

The native model picker offered Opus 5.5 and Sonnet 5.5 among its options; this
is catalog evidence, not proof of access to every listed model. Real headless
generation succeeded specifically on **claude-sonnet-5-5**, first with requested
low effort on 2.1.316, then high effort on 2.1.323. Both initializations reported
that exact model, no tools, no MCP servers and apiKeySource=none; final modelUsage
confirmed the same model. Native /effort offered low, medium, high, xhigh and max.
The documentation independently lists those levels for Sonnet 5.5. Actual
provider-applied effort was not separately reported in these generation receipts;
do not relabel requested effort as independently observed actual effort.

[Anthropic's current subscription guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
says signed-in Agent SDK and claude -p usage still draws on subscription limits;
the proposed separate programmatic allowance was paused. The
[legal/authentication guidance](https://code.claude.com/docs/en/legal-and-compliance)
permits an end user signing into the unmodified native binary, including hosted
contexts, while restricting third-party credential intermediation. This supports
the proposed personal native-CLI route; it is not authority to extract OAuth
tokens, call private usage endpoints, offer a separate login flow or enable API
billing. Service access and continued entitlement still need runtime checks.

## Real boundary observations

- Each successful smoke sent only a synthetic short JSON request, no addon or
  Workbench source. Tools, MCP, hooks, project/user settings discovery, Chrome,
  background tasks and session persistence were disabled for those calls. The
  native authentication loader still used the existing owner login.
- The native interactive /usage screen reported **usage credits are off**.
  Successful headless events independently reported isUsingOverage=false and
  overageStatus=rejected. No credits, API keys, reloads or billing modes were enabled.
  A future adapter must refuse credit-only models, fast mode and unexpected auth
  or provider substitution. A prior screen observation is not a permanent account
  lock; the production preflight and billing-change response remain unimplemented.
- stream-json emitted real five_hour and seven_day unifiedWindows. The successful
  2.1.323 smoke reported utilization 0 and 0.3 respectively, with resetsAt values
  1794616200 and 1795003200: 2026-11-14 00:30 UTC and 2026-11-18 12:00 UTC. These
  are dated provider observations, not current balances promised by this document.
  The native /usage text showed rounded percentages/times which differed slightly;
  do not silently merge readings or treat their precision/freshness as identical.
- A separate headless /usage command completed with zero model tokens and returned
  subscription percentages/reset text. This is a native command, not scraping an
  undocumented endpoint. Its text is not a stable structured percentage contract.
- Success token fields distinguish ordinary input, cache creation, cache reads and
  output. The reported total_cost_usd is a list-price estimate, not evidence of an
  API charge or a conversion between tokens and subscription percentage.
- A separate empty CLAUDE_CONFIG_DIR returned signed out (exit 1, authMethod=none).
  The real login was not expired, revoked or logged out for testing. Expiry handling
  is documented as authentication_failed and must be held for native reauthentication;
  actual expiration remains untested. Do not silently switch authentication methods.
- A two-second supervisor deadline terminated a native process waiting for input
  with exit 143. Zero user messages were sent. This proves bounded process shutdown,
  not in-flight provider cancellation or zero billing after a dispatched request.
- Real SIGINT after response fragments was checked separately on both binary
  versions. The 2.1.316 process exited 0 but returned is_error=true and
  subtype=error_during_execution, with zero aggregate result usage despite nonzero
  native response usage. The same exit/error/zero-aggregate behavior reproduced on
  2.1.323. Exit zero and aggregate zeros cannot establish success or
  no consumption. Retain observed native subtotals and unresolved aggregate usage;
  never replay automatically from those zeros. No interrupted request was replayed.
- No quota was deliberately exhausted. Real quota-exceeded, outage, expired-session
  and restarted Workbench/Claude execution checks remain to be implemented and
  tested. The CLI's documented rate-limit/authentication categories are supporting
  references, not live fault results.

The [headless reference](https://code.claude.com/docs/en/headless) documents print
mode, JSON events and SIGINT/SIGTERM differences. The
[model reference](https://code.claude.com/docs/en/model-config) documents model-specific
effort and credit-only models which can bill without consent prompts in -p mode.
The [error reference](https://code.claude.com/docs/en/errors) separates subscription
windows, authentication failure, outages and credit/entitlement failures.

## Material allocation limitation and owner decision

Neither the inspected Claude interface nor the existing Codex integration
establishes an atomic reservation of a percentage of subscription allowance, a
known upper bound for a request's percentage consumption, or a custom provider-side
percentage stop. Their readings can support **request-start cutoffs**. They cannot
guarantee that an already-started request stays below 50%, 75% or another cutoff.
External clients can also change account-wide consumption between observations;
their usage cannot honestly be attributed to Workbench.

Claude's --max-budget-usd is an estimated API-cost control; the
[CLI reference](https://code.claude.com/docs/en/cli-reference) explicitly says spend
can pass that cap. It does not solve a subscription-percentage requirement. Codex's
[documented account/rateLimits/read](https://learn.chatgpt.com/docs/app-server)
exposes allowance windows and notifications; the installed connector's existing
less-than-90% gate is also a snapshot check, not a hard percentage guarantee.
The historical Codex owner acceptance of snapshot risk does not silently change
the meaning of the newly requested 50% primary / 25-point fallback allocation.

The owner was asked to choose between:

1. Verified account-allowance request-start cutoffs, conservatively counting
   outside use against available headroom, with explicit disclosure that an
   in-flight request can cross the cutoff. Workbench can also enforce its own
   exact request-count safeguards; these are not subscription percentages.
2. Strict percentage ceilings. Hold automatic subscription dispatch when its
   worst-case allowance consumption cannot be established. Continue using the
   existing manual/native workflows until a supported strict control exists.

No answer has been assumed. Per the assignment's essential-provider-limitation
stop, strategy implementation and production Claude activation are held for this
product decision. This is not a request for renewed engineering time, API billing,
subscription changes or a quota increase. Checkpoint A's verified application
adapter, checkpoint B's connected allowance engine, checkpoint C's visual controls
and checkpoint D's release are **not complete**. No new strategy UI is installed.

## Verification and preservation

Existing Codex provider and model policy tests: **132 passed** on a disposable test
store. These cover existing production boundaries; they do not test a nonexistent
Claude adapter or strategy-selection path. No new screenshot/visual acceptance,
full local release acceptance or full hosted release acceptance is claimed.

The installed store remains exactly 942 rows / 31 tables / schema0014, logical
SHA256 66342b7a9176ffe3937a5b835b7860999739a843e4ab091600964f34ce63e588.
All four service identities and zero-restart counts match the starting observation;
the store is settled. No app installation, migration, model preference change,
gallery continuation, addon edit, map publication or legacy cutover occurred.
Unrelated WORKBENCH_ADMIN_APPROVAL edits and dependency links are preserved.
Private native receipts and preservation proofs are under
/home/rich/.local/share/TwichUI-Workbench/smart-ai-20261113. Raw transcripts stay
private; no authentication output containing identity or credentials is committed.
