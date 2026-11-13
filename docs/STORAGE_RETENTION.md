# Automatic retention

Existing worker housekeeping runs once per minute. It now also services explicit
`retention_pools` from the private installation policy. Accounting `evidence_roots`
never grant deletion authority. `retention_keep` explicitly protects required
rollback/evidence locations; current source, configuration, distributions and
history are protected independently. Browser sharing also honors required rollback
and active-process references while preserving every path and sealed byte.

`scripts/managed_storage.py` allocates new empty copies and records their identity.
It supports workspace, test-scratch, test-artifact, release and cache copies. It
never adopts old directories because their names look disposable. A producer must
close the copy successfully. Non-test copies need a bound retained result outside
all managed pools. Failed/unfinished copies, models, databases in saved use and
unknown legacy directories remain protected. `wb.py` allocates each default test
temporary run separately and closes it only after the complete command succeeds.
Explicit caller-supplied temporary storage remains unchanged.

Default retention keeps the two newest successful copies of each class for at
least 24 hours. Each pass checks 32 indexed copies, admits at most eight, targets
at most 2 GiB of reclaimed allocation and stops after eight seconds. A single
atomic file unlink may exceed the byte target. Directory deletion is limited to
2,048 entries per copy per pass. Larger copies resume in subsequent passes.

All saved SQLite rows, including historical/archived JSON and byte fields, are
checked for references. Missing, busy, malformed or oversized reference sources
hold cleanup. Open files, mappings, process arguments and working directories
hold cleanup too; unknown same-user process access is conservative. The known
nondumpable login/service-manager daemons are identified explicitly. Source and
retained-result ownership, identity and inventories are rechecked before pruning.

A durable intent precedes deletion. A short SQLite writer reservation covers the
last reference check and atomic rename into the pool's private quarantine; it
does not change stored rows and ends before recursive removal. Descriptor-relative
unlinking never follows a link outside the copy. Interrupted pruning resumes only
an unchanged subset of the original inventory. Changed/new data or references
hold the remainder. Receipts remain after the bounded active index drops completed
copies. No model is automatically removed. Invalid tiny GGUF test placeholders
can be disposable only inside a proven successful test run; usable GGUF files and
safetensors remain excluded.

Preview uses the same eligibility and budget rules without changing content,
paths, allocation receipts or the scan cursor:

```sh
python3 scripts/wb.py retention --preview --pool /absolute/managed/pool --database /absolute/history.sqlite3
```

Closed browser evidence still uses the existing archive/independent-restore/index
owner. Referenced originals are kept. Automatic archival is bounded to regular
owned browser trees of at most 64 MiB/4,096 entries. Archives, lookup records,
databases and history are never pruned. The hard recovery reserve remains 4 GiB.

The Milestone 2.5 legacy inventory has 387 pilot checkouts and 223 existing stages.
Unproven lifecycle or historical references preserve these directories. Future
producers must allocate and close managed disposable copies through this owner;
absence from a reference search alone is never cleanup permission.
