# Owner requirement: Backlog cleanup — November 11, 2026

The owner added this connected usability requirement during overview verification.
Finish the current Dashboard/visual-summary milestone and its verified private
installation first. Then deliver cleanup in the next Backlog checkpoint under
existing engineering/Git/hosted/private-preview authority. No routine renewal.

- Unobtrusive per-request Archive, Restore and Delete actions where appropriate.
- Archive removes a request from ordinary active Backlog views while retaining its
  original input, attempts, checks, complete history, results and PR links.
- Provide an Archived view with search and restoration; bulk archive eligible
  requests where practical.
- Permanent deletion requires explicit confirmation, an accurate description of
  removals, and authoritative eligibility. Refuse active execution, pending
  approval, published PRs and evidence needed for audit, recovery or publication
  consistency. Explain refusal plainly and offer Archive when safe.
- Counts, searches, Dashboard summaries and Results consistently respect archival
  state. Preserve parent/subtask and dependency relationships without orphaning.
- Keep normal cleanup simple, with secondary technical evidence when useful.
- Use existing authoritative task/database mechanisms, no competing store or
  destructive historical migration. Verify persistence, restart, permissions,
  concurrency and restore/undo semantics.
- Never archive or delete existing owner tasks for testing or deployment. Use
  disposable fixtures for all mutations; retain the real saved store unchanged.

This authorizes implementation and verified delivery, not deletion of the owner's
current tasks by the implementation agent. Archive is organizational metadata,
not cancellation, changed execution permission or withdrawal of publication.
