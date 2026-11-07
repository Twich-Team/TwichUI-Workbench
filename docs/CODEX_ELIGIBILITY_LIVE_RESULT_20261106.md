# Read-only Codex eligibility result

November 6, 2026 Mountain time. The owner approved the exact diagnostic
at `fb51e58` and launched slot 1 from the server terminal. The window
opened at 5:54:11 p.m. MST, the probe settled at 5:54:36 p.m., and the
window closed at 5:55:25 p.m. Slot 2 stayed unused. The unit proved the
connector-equivalent IP bind and private-address egress restrictions
before the fixed resolver and signed-in App Server reads. The transient
and its runtime directory were collected. The original app and Qwen
processes retained their PIDs and the 79-row store matched its fingerprint.
[Approved diagnostic](CODEX_ELIGIBILITY_PROBE_APPROVAL_20261106.md) · [Closeout receipt](/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/operator/diagnostic-closeout.json)

| Check | Redacted live result |
| --- | --- |
| Account and subscription | Signed-in ChatGPT account and eligible personal plan both observed. |
| Codex bucket and ordinary included usage | Correct bucket observed. The service explicitly affirmed ordinary included usage for this snapshot. |
| Spend control and reached-limit marker | Spend control did not report a stop. The separate reached-limit marker was null. |
| Credits | The response reported no additional credits, a zero balance and no unlimited-credit flag. Automatic reload Off remains owner-reported. |
| Primary quota window | Present, utilization below the Workbench reserve, with a current reset time. |
| Secondary quota window | Explicitly null. Utilization and reset time were unavailable for this window. This was the sole required check that held. It was neither a reported exhaustion nor a denial of ordinary usage. |
| Selected model | The exact requested model was listed as visible. An eligible generation turn is still needed to verify model entitlement. |
| Transport | Fixed resolver, App Server initialization and all three allowed reads completed. The second allowance read was unnecessary after the first reported the hold. |

The one account read, one allowance read and one catalog read settled.
The approved launcher had no task, thread, turn, cancellation, reset or
account mutation RPC. The latest probe proves the exact current hold.
The earlier acceptance receipt lacked the field-level detail needed to
prove it encountered the same response.
[Redacted probe receipt](/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/operator/probe-1.jsonl)

## Owner decision for the next acceptance package

The live probe used the former two-window requirement. The owner then
approved accepting an explicit null secondary window when the primary
window is valid and below reserve, both pre-turn reads affirm ordinary
included usage, and every other account, credit and spend check passes.
The revised policy keeps the existing hold on missing or malformed
required evidence and validates a supplied secondary window's utilization
and reset time. Secondary-window capacity remains unverified when null,
and the owner's existing snapshot uncertainty still applies.

The revised policy has been checked offline. Additional live calls and
cutover require separate authority. A8, real-provider acceptance,
installation and the required real addon bug-fix journey remain open.
[Requirements](../TWICHUI_WORKBENCH_REQUIREMENTS_v1_1.md)
