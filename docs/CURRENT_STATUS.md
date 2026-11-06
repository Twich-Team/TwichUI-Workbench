# TwichUI Workbench current status

November 6, 2026 Mountain time.

- The normal app on loopback 9487 still serves the earlier demo. The prior
  isolated Qwen run remains the only completed real-provider result.
- Candidate `133b485` supports Qwen alone, Codex alone, Qwen first with
  one approved Codex fallback, and Codex first with one approved Qwen
  fallback. Switching is limited to confirmed pre-generation conditions.
  All four choices passed offline checks. Live Codex and both live switches
  still need acceptance.
- The existing ChatGPT CLI sign-in was observed offline. Check Automatic
  reload and the additional usage-credit balance before approving any live
  Codex trial. The App Server cannot guarantee a subscription-only turn.
  Workbench holds on missing or available credit signals.
- The verified home LAN is `10.0.0.0/24` on `enp4s0`, with host
  `10.0.0.9` and gateway `10.0.0.1`. The staged HTTPS proposal accepts
  any certified computer on that subnet. The subnet matches the owner's
  intent. The operator access choice, private TLS provisioning, firewall
  preflight and cutover approval remain pending.
- The proposed app covers supported regression tests with checked local
  diffs. General bug fixes, features, inspections, simulator work, draft
  PR delivery, queue-wide pause and routine evidence retention remain open.

[Installation and acceptance record](EVERYDAY_INSTALL_APPROVAL_20261106.md) · [A8 readiness](A8_MODEL_ROUTING_READINESS_20261106.md)
