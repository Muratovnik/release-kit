# Updating a project's release-kit

## Synchronize with the installed plugin (default)

Use `relkit_sync` with `request.root` set to the explicit absolute repository:

1. `action = "status"` returns the project and target versions/hashes. `aligned`
   means identical bytes. `update_available` offers this plugin's bundled CLI;
   `project_newer` and `same_version_drift` require review, not forced replacement.
2. `action = "plan"` uses the installed CLI and bundled artifact, not GitHub latest
   or the older project's executable. It needs no `relkit_project bind`. It can
   create managed project-local scratch/locks, but never writes the projection or
   guard. A dirty checkout or invalid guard is a refusal, not permission to repair.
3. Review `result.data.plan`, `plan_sha256` and the response's `review_sha256`,
   including changed hashes and hook effects. A direct request to update this
   project already authorizes the ordinary update: do not ask for it again. Check
   project instructions for any separate hook/rollback requirement; obtain only
   missing authority. Call `action = "apply"` with `plan_hash` and
   `authorization = {"source": "user_request", "scope": "sync_update",
   "review_sha256": <plan response hash>}` when the request covers this plan.
   Otherwise omit authorization and use native confirmation. Never widen the
   request to publication, foreign hooks, commits of unrelated changes or another
   project. A declined attempt requires new user direction, not an automatic
   retry through this field or CLI.
4. Check the returned `sync.state`, updater receipt/backup and project gates.
   After a pin change old bindings expire; inspect and bind the new code before
   other project workflows. `relkit_sync` itself needs no server restart.

Rollback uses `rollback_plan` then `rollback` with its own reviewed plan hash and
`review_sha256`, using scope `sync_rollback` only when the user authorized rollback
(including recovery within an already agreed update workflow). Otherwise use
native confirmation. It runs
the installed plugin updater against the project's existing recovery receipt.
It does not restore an earlier plugin installation. Backups remain in the
project's `.git/relkit-update-*`; do not remove them without a retention decision.

An authorization hash covers the exact project/policy inputs, executor, action
and plan. If anything drifts, inspect and review again; do not mechanically copy
new hashes. Continue under existing permission only while the effects remain in
scope. Dirty tracked pins must be reviewed and checked before a project-authorized
commit; an update request alone is not permission to commit unrelated work.

The installed plugin includes `tools/relkit.pyz` and its SHA-256 sidecar. If the
user explicitly chooses manual CLI operation, that bundled CLI can run `update
--root <project> --artifact <plugin>/tools/relkit.pyz --sha256 <verified-digest>
--dry-run --json`; apply adds the reviewed `--plan-hash` and authorized `--yes`.
This reads an installed file; all generated scratch and backups stay in the
project. Never execute or trust a replacement supplied through project data.

## Other update sources and guard-only refresh

The existing `relkit_update` tool remains available through a reviewed binding
for an explicitly selected GitHub/local artifact or `refresh_guard`:

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

## Migrate an older guard with the installed CLI

If sync refuses a missing workflow pin or reviewed guard drift, request
`relkit_sync` with `action = plan` and `refresh_guard = true`. This uses the
installed plugin's CLI, so an old project CLI cannot turn a new guard requirement
into a no-op. Inspect every changed policy/workflow digest. Apply the same option
with the exact plan hash and `protect_install` authorization scope when the user
has authorized those hook effects. This refresh changes only the existing owned
guard; afterwards request a separate ordinary sync plan to update the CLI.
Neither a plugin update nor the presence of drift authorizes accepting new inputs.
