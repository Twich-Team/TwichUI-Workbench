# TwichUI Workbench current status

November 6, 2026 Mountain time.

- The normal app on loopback 9487 still serves the earlier demo. The prior
  isolated Qwen run remains the only completed real-provider result.
- Candidate `2613172` supports Qwen alone, Codex alone, Qwen first with
  one approved Codex fallback, and Codex first with one approved Qwen
  fallback. Switching is limited to confirmed pre-generation conditions.
  All four choices passed offline checks. Live Codex and both live switches
  still need acceptance.
- The existing ChatGPT CLI sign-in was observed offline. Check Automatic
  reload and the additional usage-credit balance before approving any live
  Codex trial. The App Server cannot guarantee a subscription-only turn.
  Workbench holds on missing or available credit signals.
- The owner approved the home subnet `10.0.0.0/24` as the operator access
  boundary. The revised staged HTTPS listener uses server certificate
  verification at `10.0.0.9:9487`. Cutover still requires approval,
  private server TLS provisioning and scoped firewall preflight.
- The proposed app covers supported regression tests with checked local
  diffs. General bug fixes, features, inspections, simulator work, draft
  PR delivery, queue-wide pause and routine evidence retention remain open.

[Installation and acceptance record](EVERYDAY_INSTALL_APPROVAL_20261106.md) · [A8 readiness](A8_MODEL_ROUTING_READINESS_20261106.md)
