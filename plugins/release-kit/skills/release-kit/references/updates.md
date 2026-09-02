# Updating a project's release-kit

1. Request `relkit_update` action `plan` with the intended source. A local artifact
   must be project-relative with its reviewed SHA-256; stage it in an ignored,
   project-owned location. The plan is not permission to execute candidate code.
2. Review changed projection and guard hashes, guarded policy inputs, version and
   backup destination. Resolve input drift separately. A dirty worktree is not a
   reason to stash or bypass the updater's refusal.
3. Obtain any separate hook/rollback permission required by project instructions.
   Call action `apply` with identical source fields and the returned `plan_sha256`
   as `plan_hash`; the client must obtain human confirmation of the exact write.
4. Check the result, backup/receipt and required owner gates. If the projection or
   guard changed, discard the old binding, inspect the new bytes and bind again.
   Do not repin silently or assume plugin installation updated the project.

For rollback, use `rollback_plan`, review it and then `rollback` with its own
plan hash and confirmation. Reconcile partial update receipts before retrying.
Keep backups within the project; another location needs agreement on its exact
path and retention. Do not remove recovery receipts just because a check passed.

The CLI equivalent is `update --dry-run --json`, followed by the same source
selection with `--plan-hash <reviewed hash> --yes --json`. `--yes` acknowledges
permission already obtained; it cannot grant permission or bypass project rules.
