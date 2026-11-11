# Schema0014 administrator compatibility — November 11, 2026

Backlog cleanup needs two additive tables in the existing authoritative database.
The installed [helper's settlement check](../ops/workbench_admin.py:548) accepts
schema 0012/0013. Its named grant has no operation to replace root-owned helper
bytes. The existing approved executor remains session78, with the same expiry;
no new or broader grant is requested.

[The prepared owner script](../ops/upgrade_workbench_admin_schema 0014.sh) permits
exactly one substitution in the installed helper: add 0014 to the accepted schema
list. It verifies the complete old and new hashes, root paths and existing lock;
creates a root-owned backup; replaces the helper atomically; and verifies the grant
bytes remain identical. It makes no service, firewall, authentication, network,
session, expiry, model or data change. It refuses differing inputs or an existing
backup. No helper status command is run from the owner's differently bound login.
Codex verifies status afterward from the already-approved executor.

Installed helper expected hash:
`d7c20bde1a7fe764caca764965a469ddf6f4516c709747da2984dd4085cabdae`.
Replacement expected hash:
`72ceb24137afba5ebab832a4bc15d3784f49090f7c3e67998d4a800ac6b0e0a4`.
Backup: `/var/lib/twichui-workbench-admin/helper-before-schema 0014.py`.

After verification is complete, the required owner terminal action is:

```bash
cd /home/rich/.codex/worktrees/everyday-workbench/TwichUI-Workbench
printf '%s\n' '67f4028083c1f6da3636f9d74668271f9c5bb4965c9a820edf97060f72d59d74  ops/upgrade_workbench_admin_schema 0014.sh' | sha256sum -c - && bash ops/upgrade_workbench_admin_schema 0014.sh
```

Enter any sudo password only in that terminal. This is a new schema 0014 compatibility
copy, not a rerun of the already successful0013 upgrade or bootstrap. Do not change
the grant binding or ask the owner to query normal helper status from their login.

After this action, Codex can independently verify the replacement, unchanged
approved executor/services/store, then complete the exact verified private build's
copy/migrate/seal/install/browser/restore/rollback sequence. Migration operates on
a fresh copied store; the prior109store remains intact for rollback. No owner task
is archived or deleted as part of deployment acceptance.
