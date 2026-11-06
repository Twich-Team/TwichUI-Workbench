# TwichUI Workbench current status

November 6, 2026 Mountain time.

- The normal app on loopback 9487 still serves the earlier demo. The
  prior isolated Qwen run remains the only completed real-provider result.
- The seal for `2613172` matched. The proposed release requires explicit
  approval for one Qwen-only cutover and one bounded local trial. All four
  provider choices passed earlier offline checks. Live Codex and both
  live switches are outside this release.
- The owner reports a zero additional usage-credit balance and Automatic
  reload Off. These are owner-reported settings. The pinned App Server
  still offers only snapshots and no per-turn billing lock. The connector
  stays inactive and its account-review marker stays absent.
- The owner-approved home subnet is `10.0.0.0/24`. The staged HTTPS
  listener at `10.0.0.9:9487` verifies the server certificate and uses no
  client certificate or dashboard passphrase. Private server TLS and
  effective firewall verification are cutover steps.
- The limited release covers supported Lua regression tests with checked
  local diffs. General bug fixes, features, inspections, simulator work,
  draft PR delivery, queue-wide pause and routine retention remain open.

[Installation and acceptance record](EVERYDAY_INSTALL_APPROVAL_20261106.md) · [A8 readiness](A8_MODEL_ROUTING_READINESS_20261106.md)
