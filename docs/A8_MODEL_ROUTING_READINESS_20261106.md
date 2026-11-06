# A8 signed-in Codex and model routing

November 6, 2026 Mountain time. The bidirectional candidate is `3404a29dc1e62ac3ce1c6093921584dce13053ca`. It supersedes `8167291`, whose fallback could only go from Codex to Qwen. The running app on loopback 9487 still serves the earlier demo build.

| Task choice | Implemented and checked offline | Live evidence |
| --- | --- | --- |
| Qwen alone | Saved local model and no fallback. Two different browser tasks reached independently checked results in the disposable everyday fixture. | One previous isolated Qwen task completed and restored its model service. Fresh installed everyday acceptance is pending. |
| Codex alone | Saved signed-in Codex model and no fallback. Simulated planning, separate run approval and independently verified implementation passed. An incorrect edit was refused with usage retained. | The local CLI previously reported ChatGPT sign-in. Real account, allowance, planning and implementation through Workbench are pending. |
| Qwen first, Codex fallback | One switch after a connection refusal from Qwen's read-only model check, before Workbench sends its generation request. Simulated Codex produced the plan and ran only after separate approval. A disabled or changed policy blocked the switch. | Pending. The combined request uses a temporary private network namespace for only the candidate worker. |
| Codex first, Qwen fallback | One switch on proven pre-generation connector unavailability, sign-in failure or the included allowance reserve when the selected condition allows it. Simulated unavailability and allowance holds switched once under the original planning claim. | Pending. The combined request exercises an absent connector with the model service undisturbed. |

## When Workbench stops

| What Workbench observed | Effect |
| --- | --- |
| Proven unavailability before generation | Only the narrow conditions above can consume the one allowed switch. Models must permit the direction and condition, and the individual task must opt in. |
| Definitively finished without a usable response | Keep the failure and reported usage. Hold for review. Failed checks also hold. |
| Partial output | Keep the distinction and any observed usage. Hold for review. If a terminal receipt is missing, completion remains uncertain even when some text arrived. |
| Active or uncertain attempt | Keep the original claim. Close or interrupt the owned transport when possible, retain known observations, and hold without switching or replaying. |

The two directions can be enabled together in Models. Older saved settings retain their existing fallback order unless the owner explicitly sets a policy for eligible tests. Individual requests default to no switch. The worker reserves one generation claim, checks the original consent, scope, selected models and policy again before a switch, and records that the first provider started no generation. The reviewed plan keeps the provider that produced it. Any implementation needs separate approval and independent checks. The implementation stage has no provider switch.

## Connection and permissions

The pinned CLI is `0.166.0`. Local help and its exported schema expose non-interactive `exec`, stdio App Server, managed account read, included usage snapshots, model catalog, ephemeral thread, structured turn output, usage events and interruption. This private Workbench uses App Server for those protocol controls. The supported sign-in route is the owner's existing ChatGPT authentication. The owner supplied the subscription price. Actual current inclusion and capacity come from the account and provider protocol. [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) · [Codex authentication](https://learn.chatgpt.com/docs/auth) · [Codex App Server](https://learn.chatgpt.com/docs/app-server)

The current owner UID is Rich, 1000. The separate Rich-owned connector uses a private Unix socket, matches worker UID and cgroup, and starts the pinned CLI with a cleared environment in a new mount, user and process boundary. It mounts only the existing private sign-in file read-only, the pinned executable and the trust and DNS files needed for network access. Its temporary home and working directory start empty. Shell and network tools, hooks, plugins and multi-agent features are disabled. The thread must report read-only mode, no approvals and no external instruction files. The connector is the only new component that needs outbound network access. Workbench task code receives only the normalized provider result. The pinned CLI refresh and revoke endpoint overrides point to a closed loopback port. Read-only authentication can hold when refresh is necessary. The owner can resolve that through the existing CLI login flow under separate authority. [Pinned Codex auth implementation](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/login/src/auth/manager.rs)

The supported interface can expose account type, a backend included-usage decision, quota windows and reported turn tokens. Workbench requires an affirmative included-usage decision, known current primary and secondary windows below 90 percent and no reported credits or unlimited balance. The owner's account-level purchase settings have not been verified. The owner must confirm paid usage and automatic purchases are off before the connector may be started. Workbench holds on missing accounting. The 32,768-token threshold is evaluated on observed turn usage. Hard local controls are one turn per phase, packet and stream sizes, deadline, one exact edit and zero automatic retries. Local cancellation closes the socket and owns the process shutdown. Provider-side completion may remain uncertain and is recorded as such. There is no monetary balance calculation or provider-enforced subscription token ceiling.

The isolated App Server account check timed out with networking disabled. That leaves live access unverified. This offline result establishes no entitlement decision. Signed-in Codex availability and both live fallback directions remain in the [installation and acceptance record](EVERYDAY_INSTALL_APPROVAL_20261106.md).
