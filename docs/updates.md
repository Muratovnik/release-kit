# Project updates and recovery

Each adopting repository tracks its own `.github/relkit.pyz`. Updating the source
repository, a Python package, a plugin or one adopter does not update the others.
For a first installation, use the [quick start](../README.md#first-check-in-an-existing-project).

## Updating a project

From a project containing a 0.6.0-or-newer projection:

```text
python .github/relkit.pyz update --dry-run
python .github/relkit.pyz update
```

If the Python package is installed, the equivalent command is `relkit update`.
Both update this project's projection, not the global Python package, source
checkout, other projects, policy or CI. No alias or global installation is created.
A [plugin](plugin.md) can instead explicitly synchronize a project to its bundled
CLI with `relkit_sync`; installing the plugin alone changes no project.

The default source is the GitHub repository recorded by the distribution builder.
Use `--repository OWNER/REPO` when no source is recorded or to explicitly choose a
different trusted publisher. `--release v0.6.0` selects a particular stable release;
otherwise the latest published non-prerelease release is used. A branch or tag is
not an update source: the publisher must attach `relkit.pyz` to a published release.
The updater refuses downgrades and different bytes bearing the installed version.

GitHub transport uses the optional `gh` CLI and its existing authentication,
including access to private repositories. The native CLI owns authentication and
release download; release-kit stores no token or new authentication flow. The
[release metadata](https://docs.github.com/en/rest/releases/releases#get-the-latest-release),
[asset digest](https://docs.github.com/en/rest/releases/assets) and
[download](https://cli.github.com/manual/gh_release_download) must agree on size,
digest, embedded repository and version. Missing digests are refused. This is an
integrity check against a trusted publisher, not an independent publisher signature.
The release coordinator's signed-publication verification is a separate operation.

For an offline or locally built candidate, supply an independently reviewed digest:

```text
python .github/relkit.pyz update --artifact /path/to/trusted/relkit.pyz --sha256 DIGEST --dry-run
python .github/relkit.pyz update --artifact /path/to/trusted/relkit.pyz --sha256 DIGEST
```

Local-file updates do not need `gh`. Before writing, the command shows old/new
versions, paths and SHA-256 values for the projection and any existing owned guard.
Normal updates require a clean checkout, tracked projection/configuration, ordinary
in-repository Git metadata and a valid existing guard if installed. They do not
auto-stash. Linked worktrees, aliased update paths, Git directory/index environment
overrides, arbitrary hooks and incompatible external dispatchers are refused.
Protected pre-0.5.0 installations need the [guard-owner migration](#migrating-the-guard-owner).

Interactive use asks for confirmation. Non-interactive use refuses unless `--yes`
is supplied after the exact changes have been approved. Separate hook permission
required by a project's instructions remains required. The flag acknowledges
approval; it does not override authorization.

`--dry-run` inspects/downloads but never executes the candidate, persists a backup
or changes the projection/guard. A temporary repository-local lock serializes runs.
For structured previews and drift rejection, use `--json`, then apply the same
request with `--yes --plan-hash HASH`; see [reviewed plans](cli-json.md#reviewed-update-plans).

After confirmation the updater checks the candidate runtime version, saves original
files and modes in `.git/relkit-update-<id>/`, records `.git/relkit-update.json`, and
runs the new projection's worktree audit. It refreshes only an already-installed,
intact repository guard and verifies it. An external dispatcher is checked, not
modified; no new guard is installed. `--no-download` also disables audit-engine
downloads. A failed audit restores the old artifact/guard. Concurrent edits that
make restoration unsafe are preserved, and the backup is reported for recovery.

Review the projection diff, run project tests, commit, then run the clean-history
publication audit, with `--owner` where applicable. Updates do not stage, commit,
push, publish, change refs, relax policy, enable opt-in rules or edit workflows.
Their worktree check is not the final owner publication verdict.

## Already-updated files and guard drift

If a projection or configuration was manually changed, `protect check` names each
changed input with pinned/current SHA-256 values. Review those changes and obtain
any required hook permission before:

```text
python .github/relkit.pyz update --refresh-guard --dry-run
python .github/relkit.pyz update --refresh-guard
python .github/relkit.pyz protect check
```

This mode permits a dirty checkout, checks current artifact/policy, backs up the old
guard and changes only the supported guard's trust pins. It does not download a
release or edit the projection/configuration. Normal update refuses existing drift
rather than treating an unrelated update as permission to trust it. Custom or
modified hook templates need manual owner review.

The same mode adopts a guard written by an earlier release-kit. An older template
pinning current inputs is not drift and keeps enforcing them: `protect check`
reports it, and refresh rewrites it under backup, validation and rollback. It is
not an emergency requiring a bypass.

## First update and rollback

Older embedded versions have no update command. Build or obtain a trusted 0.6.0+
zipapp once and use it to update the older tracked copy:

```text
python /path/to/trusted/relkit.pyz update --root . --artifact /path/to/trusted/relkit.pyz --sha256 DIGEST
```

An external zipapp can also run `update --root . --refresh-guard` when an older
project CLI lacks repair support. Future updates use the project's own command;
building release-kit does not migrate projects.

Restore the most recent transaction, including guard-only refresh, with:

```text
python .github/relkit.pyz update --rollback
```

Keep the trusted external updater available if the restored version predates this
command. Rollback verifies backups and refuses later edits to guarded inputs/hooks.
It does not reset Git or discard user changes. After a crash, inspect the PID in
`.git/relkit-update.lock` and remove only that lock after proving its process stopped.
A pending receipt requires rollback before another update. Exit `0` means success,
no-op or dry-run; `2` means refusal or operational failure, not proof of no effects.

## Backup retention

One receipt names one backup; earlier backups cannot be restored by `--rollback`.
A completed update reports how many remain. `update --prune-backups` removes exactly
those superseded backups, never the current receipt's backup or a directory with
anything other than updater-owned files. Preview with `--dry-run` before approving.
Backups otherwise remain retained recovery data, not generic disposable cache.

## Migrating the guard owner

This section applies to protected **0.3.x/0.4.x** installations migrating across the
0.5.0 ownership boundary, not to first-time installation of the current CLI.
Older installers could create/replace a redirected dispatcher; 0.5.0 and newer
treat it as externally owned.

Save the existing projections, public policies, repository guards and redirected
dispatcher bytes. Have the external hook owner install an executable dispatcher
with `# git-common-dir-hook-dispatcher: pre-push v1` and verify that it invokes the
Git common-directory hooks. Project the same reviewed 0.5.0-or-newer compatible
artifact/policy into every affected adopter. Run `protect install`, `protect check`
and, in each clean adopter, `audit --history --owner` before accepting the migration.

Never refresh a guard with a pre-0.5.0 installer after migrating the dispatcher.
Rollback restores the saved artifact, policy, repository guard and dispatcher as
one coherent set; mixing the two ownership contracts can select the wrong hook.
No migration step grants release-kit permission to alter a separately owned hook.
