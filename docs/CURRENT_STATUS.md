# TwichUI Workbench current status

November 6, 2026 Mountain time.

- The earlier app is restored at `http://127.0.0.1:9487/` under the two
  sealed rollback services. Its 79 history rows match the original
  fingerprint, and the original Qwen and dashboard processes kept running.
- The exact `d13c2e6` cutover for `92094bc` passed system-level filtering,
  sandbox, history, and LAN HTTPS checks. The scoped firewall rule was
  removed during rollback. Current access is loopback.
- The first Codex-only planning request held on an unexpected protocol
  response before generation. It produced no plan or run offer. The other
  three trials never started. The failed intake, candidate seal, original
  approval record, and both independently restored archives are retained.
- The owner-reported zero extra credits, Automatic reload Off, and accepted
  snapshot uncertainty remain recorded. Live signed-in eligibility and A8
  acceptance remain open. API-key billing, extra credits, purchases, auto
  reload and upgrades remain excluded.
- Supported existing Lua regression tests remain the candidate's release
  scope. General addon bug fixing, features, inspections, simulator work and
  broader initial-product acceptance remain open. Visual styling and the
  full requirements audit are later phases.

[Approved package](EVERYDAY_SYSTEM_INSTALL_APPROVAL_20261106.md) · [A8 readiness](A8_MODEL_ROUTING_READINESS_20261106.md) · [Live closeout](/home/rich/.local/share/TwichUI-Workbench/everyday-system-20261106-acceptance/acceptance-closeout.json)
