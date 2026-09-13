# Structured CLI contract

Available since release-kit 0.8.0. Use the project's pinned
`.github/relkit.pyz`; a global package may be a different version. This process
interface needs no MCP server, service registration or Python runtime dependency.

```text
python .github/relkit.pyz --version --json
python .github/relkit.pyz audit --json
python .github/relkit.pyz notes v1.2.0 --json
python .github/relkit.pyz release plan v1.2.0 --json
python .github/relkit.pyz release status v1.2.0 --json
python .github/relkit.pyz update --dry-run --json
```

`--json` may appear before or after the command. Without it, `release plan`
prints a short human-readable plan and continuation command; use JSON for the
complete object. `--help` displays usage instead of a result envelope.

## Process boundary

Stdout contains one JSON object and a newline. It is UTF-8 compatible; non-ASCII
text may be escaped. Progress, native engine output and unexpected tracebacks go
to stderr; release project-command output remains in the owned run log. Do not
reconstruct semantic fields by parsing those diagnostics. Termination, an absent
interpreter or a broken pipe may prevent the final response: missing/truncated
JSON is an unknown result, never success.

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`, independent of package and receipt versions |
| `tool_version` | Running release-kit version |
| `command` | String array, such as `["release", "status"]` or `["version"]`; empty on argument errors |
| `root` | Absolute project path; `null` for version and argument errors |
| `status` | `ok`, `failed`, `refused` or `pending`, corresponding to the process exit |
| `exit_code` | Process exit: `0`, `1`, `2` or `3` |
| `data` | Command-specific object; may contain partial results after failure |
| `errors` | Objects with stable `code`, human `message`, optionally `path` and one-based `line` |
| `warnings` | Non-fatal objects with `code`, `message`, optionally `path` |
| `next_action` | Suggested argument array or `null`; not a shell string or permission |

Check the actual process exit, matching envelope `exit_code`, supported schema and
command-specific verdict. Tolerate additional fields and unknown error codes;
unknown status/schema must fail closed. Human messages, ordering and whitespace
are not stable identifiers. Exit `2` can follow a safely rolled-back update and
must not be read as proof of no side effects.

Error codes include `invalid_arguments`, `configuration_error`, `io_error`,
`check_error`, `check_failed`, `engine_error`, `protection_error`, `invalid_notes`,
`missing_notes`, `confirmation_required`, `update_error`, `release_error`,
`release_pending`, `release_cleanup_unconfirmed`, `interrupted`, `internal_error`
and fallback `command_failed`.
`lock_retained` is a warning.

`release_cleanup_unconfirmed` retains the release lock and recovery evidence.
`data.process_cleanup` and, when available, `data.release.process_cleanup` are
`unconfirmed`; `next_action` is `null`. Verify the owned commands and descendants
have stopped before removing the exact retained lock. A stopped parent PID is
insufficient, and an unknown remote outcome still needs reconciliation.

## Command payloads

### Version, checks and notes

`--version` returns `data.engines`, mapping engine names to version strings.

`exposure` returns `data.exposure`: `new` and `baselined` findings with `path`,
`kind`, `detail`, plus `stale`, `unreadable` and `excluded` string arrays.

`audit` adds `scope` (`worktree`, `staged`, `history`) and `engines`, mapping
engine names to native exit codes or `null` when not completed. History adds
`untracked_present`: untracked entries do not by themselves violate the clean
tracked-tree precondition; configured worktree candidate checks still apply.
Native engine diagnostics are not invented structured findings. The top-level
exit also includes history, guard and overlay failures.

`notes` returns validated `notes` including its final newline, requested `version`
and `output` (absolute export path or `null`). An export failure may still return
validated text, but is not success. Structural notes validation stays Git-free.
See [notes validation and safe export](notes.md).

`overlay` returns `verified_mounts` (number checked) and `skipped` (out-of-scope
mounts). Inspect `errors` before concluding that checked mounts are valid.

`protect` returns `guard` (`valid`, `invalid`, `installed`) and the installation
`path`. A valid old template adds `guard_template_outdated`; its pins remain
active. `install --dry-run` returns `data.plan` with exact before/after hook
hashes, guarded inputs, dispatcher identity and `plan_sha256`, without writing a
hook. Pass `install --plan-hash HASH` to reject drift. Review project-required
hook permission separately. [Guard updates](updates.md) retain backup/recovery.

### Release planning and execution

`release next` returns `data.next` with published `previous` identity, selected
`version`/`tag`, explicit `bump` and separate `tag_state` (available/occupied).

`release prepare` returns `data.candidate`: attempt, plan fingerprint, CI identity
where applicable, outcome and receipt/log paths. Passing preparation appears in
subsequent `data.plan.candidate`; it never creates a stable tag.

`release plan` returns `data.plan`, including `plan_sha256`, pinned SHA,
predecessor, exact refspecs, assets, future actions, `workflow_jobs`
(declared/unverified/optional), planning `host` and operator `caveats`. Caveats
also print on stderr. Publisher determines whether planning queries a host.

Since 0.21.0, `settings.publisher` distinguishes `directory`, `github` and
`github-actions`. Local prepare records `candidate.kind = local`, attempt and
file digests. Directory plans have no remote pushes or workflow ID. Missing
publisher fields in older receipts retain their original Actions semantics.

`release run/resume` returns `data.release` once a run is recorded. It includes
`observation: "current-run"`, receipt/log paths, tag, SHA, plan hash, recorded
tool version, complete plan, CI identity, artifacts, stage/stages, publication,
verification, cleanup, verification time/platform, local-change flag and retained
scratch. Unknown fields are `null`. A publication may exist while verification
fails; retained cleanup is independent of both.

`release abandon` records `outcome.status = "abandoned"`, reason and time in the
local receipt; `next_action` is `null`. Missing reason, existing remote tag or
release/draft, published release or publication flags refuse with exit `2`.
It changes no ref. A subsequent run reports `data.archived_receipt`.

### Release status: read a saved receipt

```text
python .github/relkit.pyz release status v1.2.0 --json
```

Returns the release view with `observation: "local-receipt"`. Independent fields
are `tag_state` (`absent`, `local`, `pushed`), `publication_state` (`absent`,
`draft`, `published`), `ci_verdict` (`unknown`, `pending`, `failed`, `passed`,
`not-required`) and `acceptance` (`incomplete`, `accepted`). Unknown CI in old
receipts is not inferred to have passed. Legacy `publication = not-pushed` means
no publication was observed, not that a tag is absent.

Status reads a bounded schema-1 receipt and checks its plan digest, checkout
identity and pinned committed inputs. It does **not** contact GitHub, execute
project commands, create a lock, append logs, clean files or rerun verification.
Exit `0` means the receipt was read, even when its recorded verification failed.
Missing, unsafe, malformed or unsupported receipts return `2`. Local edits are
untouched; `local_changes` and recorded outcomes are historical, not a fresh scan.

`resume_version_matches` says whether the current tool matches the saved plan.
Reading an old schema-1 receipt neither migrates it nor enables cross-version
resume. A local receipt is not cryptographic evidence of current remote state.

### Release verify: perform verification again

```text
python .github/relkit.pyz release verify v1.2.0 --json
```

Verify re-observes an existing publication using its saved receipt. It creates
local verification state/locking and diagnostics, downloads assets for hosted
publishers, verifies the applicable signatures, and **executes the pinned smoke
commands**. Directory delivery verifies the saved local destination instead of
contacting GitHub. This is not a read-only receipt inspection.

It does not prepare/push a tag, create a release or authorize publication.
Compatible old plans retain their bytes/fingerprint; `verifier_version` records
the current verifier. `verification = passed` with failed required CI still
returns `1` and `acceptance = incomplete`; inspect `ci_problems`. Local publishers
report `ci_verdict = not-required`, not a fabricated passing CI run. Missing
publication, identity drift and invalid signatures remain failures.
`next_action` suggests `verify` for an unverified publication, without `--publish`.

### Update and rollback

`update` returns `data.plan`, `plan_sha256` and `state` (`planned`, `unchanged`,
`pending`, `installed`, `rolled-back`, `pruned`). Rollback reports
`action: "rollback"` and receipt path with the restoration plan. Backup/receipt
paths appear after transaction creation; failures retain known progress.
`--prune-backups` reports `action: "prune-backups"`, `superseded` (backups no
receipt can restore), and, after confirmation, `removed`. Preview stops after
`superseded`.

## Reviewed update plans

`update --dry-run --json` does not execute candidate code, replace the projection,
refresh a hook or create a rollback backup. It may query GitHub, download a
candidate and temporarily create a project-local lock/scratch directory. It is
not a promise of zero filesystem activity.

The schema-1 plan records tool version, root, action (`update`, `refresh-guard`,
`noop`), old/new versions and artifact hashes, publisher, exact changed `files`
with relative paths and before/after SHA-256, unchanged guarded `inputs`, and
`guard_inputs_before`/`guard_inputs_after`. No-op has no changed files. Fingerprints
are SHA-256 of UTF-8 JSON with sorted keys and compact separators, excluding the
fingerprint itself.

After review, repeat the same source selection or `--refresh-guard`, passing
`--yes --plan-hash HASH`. Changed plans refuse before candidate execution or
backup creation. A fingerprint is equality evidence, not a signature, sandbox
or human approval.

`update --rollback --dry-run --json` validates backups without restoring them;
its plan binds the receipt hash and restored file hashes/modes.
`update --rollback --yes --plan-hash HASH` rejects stale restoration plans.
Source selection/guard refresh cannot be combined with rollback.

JSON update mode never prompts, even on a terminal, and requires `--yes` for
non-dry-run invocations including possible no-ops. Project hook permissions remain
separate. Release run/resume need `--publish` after review. Neither `next_action`,
receipts nor matching hashes grant write authority. Keep executable and arguments
separate; never evaluate diagnostic text as shell code.

## Acceptance boundary

The test suite uses isolated Git repositories, checks/smokes and subprocesses;
it must not publish. A live push, hosted CI and immutable publication test still
needs an explicitly agreed disposable repository and exact refs/actions. Never
rewrite existing releases to satisfy a test. Preparation, publication, CI,
verification, cleanup and platform coverage are separate outcomes; consult the
[local](local-releases.md) or [Actions](release-coordinator.md) contract for the
selected publisher.
