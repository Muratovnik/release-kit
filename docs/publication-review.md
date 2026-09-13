# Reviewing a previously private publication

This is the maintainer's release/disclosure procedure, not a declaration that this
repository or its history has already passed it. Code quality, installability and
permission to disclose bytes are separate decisions. No command here changes
repository visibility, rewrites history or publishes a release automatically.

## Freeze what will be disclosed

Record the source commit, repository visibility, intended refs/releases and the
artifact names/digests. Use an authorized complete local checkout. Identify
uncommitted/generated inputs separately; an API tree or source ZIP is not a clone
with history. Confirm the Git root, shallow state and available refs:

```text
git rev-parse --show-toplevel
git rev-parse HEAD
git rev-parse --is-shallow-repository
git for-each-ref "--format=%(refname)"
```

Quote the format argument as required by the shell. Fetch the relevant hosted refs
in the reviewed checkout or an explicitly approved disposable clone. Do not replace
local tags/refs or follow unrelated private siblings without reviewing that effect.
PR/MR/change refs that are not fetched remain outside local scanning.

Run the existing full check and publication audit, with the source import path set
to this checkout: `$env:PYTHONPATH = "src"` in PowerShell or
`export PYTHONPATH=src` in Bash. See [contributing](../CONTRIBUTING.md) for setup:

```text
python tools/check_distribution.py
python -m releasekit.cli audit --history
```

Use the actual owner policy and `--owner` when there are owner-private values to
check. Do not invent a dummy policy to obtain an owner pass. A dirty-tree refusal,
missing scanner or incomplete clone is an unresolved check, not a safe result.
Review legitimate synthetic fixtures rather than broadly excluding new directories.

## Review every exposed surface

| Surface | Evidence to collect | What the repository audit does not establish |
| --- | --- | --- |
| Current source and reachable history | Commit/refs, full-history scanner result and semantic review | Unfetched objects, private intent unknown to the policy |
| Published releases, old versions and source archives | Paginated inventory, downloaded files, names/sizes/digests, archive inspection | Uploaded files need not be tracked by Git; a current-tree cleanup does not remove them |
| Actions runs, all retained attempts, logs and artifacts | Inventory and direct review of accessible downloads/logs | Hosted diagnostics are not Git blobs and can contain command output |
| Issues, PRs/reviews, comments, attachments and enabled Discussions/wiki/Pages | Inventory and review appropriate to each enabled feature | Public source does not imply those materials were inspected |
| Plugin/native installation | Downloaded package identity, SDK startup, native client/version and first read | A hash check or stdio smoke alone does not prove client discovery |

For GitHub use its native CLI/API to enumerate **all pages** of releases, workflow
runs and artifacts, then download the specific reviewed items. Store sensitive
inventories/downloads under an ignored project-local directory, never in public
evidence documents. Preserve access errors and expiration as gaps. Do not turn a
404 into proof that no artifact/log existed, or one successful run into all-attempt
coverage. Native tools and the existing scanner are preferred over another custom
secret/link parser.

Review archive bytes with the current trusted inspector, not code executed from an
old release. Compare package inventories with actual archives. Include files that
are not linked from README; navigation does not control what recipients can obtain.
Logs may contain private command output. Do not paste unredacted logs, credentials,
private-name lists or raw matches into an issue, PR or this document.

GitHub describes the disclosure effects of a visibility change in its
[visibility documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility),
including public access to Actions history/logs when a private repository becomes
public. Verify the current host behavior before performing the change.

## Decide and retain evidence

For each surface record the exact identity/scope, actual check, result and unresolved
portion. A useful summary distinguishes passed, failed, partially checked and not
verified. A scanner pass is not a semantic audience-fit review, and a passed test is
not approval to disclose the fixtures or diagnostics it used.

Keep sensitive receipts local. A public summary may name the source/artifact digests,
commands and bounded conclusions without exposing the protected values. Do not claim
that an incomplete remote inventory or an unavailable native run passed.

A confirmed credential exposure needs revocation/rotation and an owner-reviewed
remediation plan; deleting the current file alone does not remove past disclosure.
Do not rewrite history or delete releases/logs speculatively. Recheck affected
surfaces after any approved cleanup. Finally, recheck source/asset drift and obtain
the maintainer's explicit decision to change visibility or publish.

A PR implementing this procedure is not its execution receipt. No new signing
system, paid CI, repository visibility change or historical deletion is required
merely to make the development checks work.
