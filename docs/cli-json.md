---
status: draft
---

# Structured CLI contract

Available since release-kit 0.8.0. This is an opt-in process interface, not an MCP
server. Use the project's pinned `.github/relkit.pyz`; a global package may be a
different version. No service registration or Python runtime dependency is needed.

```bash
python .github/relkit.pyz --version --json
python .github/relkit.pyz audit --json
python .github/relkit.pyz notes v1.2.0 --json
python .github/relkit.pyz release plan v1.2.0 --json
python .github/relkit.pyz release status v1.2.0 --json
python .github/relkit.pyz update --dry-run --json
```

`--json` may appear before or after the command. Without it, the existing text
interface remains available; notably `release plan` still prints its original plan
object. Help (`--help`) always displays human-readable usage and exits.

## Process boundary

For JSON command invocations, stdout contains exactly one JSON object and a newline.
It is UTF-8 compatible (non-ASCII text may be escaped). Progress, native engine output
and unexpected tracebacks go to stderr; release project-command output remains in
the owned run log. No semantic field is reconstructed by parsing these messages.
An OS termination, unavailable interpreter or broken output pipe cannot promise a
final response. Missing/truncated JSON is an unknown result, never success.

Every envelope contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`; independent of package and receipt versions. |
| `tool_version` | Runtime release-kit version. |
| `command` | String array, e.g. `["release", "status"]` or `["version"]`; empty on argument errors. |
| `root` | Absolute project path; `null` for version and argument errors. |
| `status` | `ok`, `failed`, `refused`, or `pending`, corresponding to exit code. |
| `exit_code` | Process exit: `0`, `1`, `2`, or `3`. |
| `data` | Command-specific object; partial results remain available on failure. |
| `errors` | Objects containing stable `code`, human `message`, optionally `path` and one-based `line`. |
| `warnings` | Non-fatal objects with `code`, `message`, optionally `path`. |
| `next_action` | Suggested argument array, or `null`; not a shell string or permission grant. |

Clients must check the process exit code, matching envelope `exit_code`, supported
`schema_version` and the command-specific verdict. Unknown additional fields and
error codes should be tolerated; an unknown status/schema must fail closed. Human
messages, ordering, whitespace and diagnostic wording are not stable identifiers.

Codes include `invalid_arguments`, `configuration_error`, `io_error`, `check_error`,
`check_failed`, `engine_error`, `protection_error`, `invalid_notes`, `missing_notes`,
`confirmation_required`, `update_error`, `release_error`, `release_pending`,
`interrupted`, `internal_error` and fallback `command_failed`. `lock_retained` is a
warning. Exit `2` can mean an operational error, including a safely rolled-back
update; it must not be interpreted as proof that no side effects occurred.

## Command payloads

- `--version`: `data.engines` maps engine names to version strings.
- `exposure`: `data.exposure` carries `new` and `baselined` findings, each with
  `path`, `kind`, `detail`; plus string arrays `stale`, `unreadable`, `excluded`.
- `audit`: the same exposure report, `scope` (`worktree`, `staged`, `history`),
  and `engines` mapping names to native exit codes or `null` when not completed.
  Native engine diagnostics are not presented as invented structured findings.
  The top-level exit also includes history, guard and overlay failures.
- `notes`: validated `notes` including final newline, requested `version`, and
  `output` (absolute exported path or `null`). Export errors can leave validated
  notes in `data` but do not return success. Structural validation stays Git-free.
- `overlay`: `verified_mounts` counts mounts checked, `skipped` names out-of-scope
  mounts. Inspect `errors` before treating checked mounts as valid.
- `protect`: `guard` (`valid`, `invalid`, `installed`), with `path` on installation.
- `release plan`: `data.plan` is the existing described plan, including
  `plan_sha256`, pinned SHA, previous tag, exact refspecs, assets and future actions.
- `release run/resume`: `data.release` appears once a run is recorded. It includes
  `observation: "current-run"`, receipt/log paths, tag, SHA, plan hash, recorded tool
  version, CI identity, artifacts, stage/stages, publication, verification, cleanup,
  verification timestamp and platform, local-change flag and retained scratch paths.
  Not-yet-known fields are `null`. Publication and verification are separate:
  a published release can fail verification. Retained cleanup is also independent.
- `release status`: the same release view with `observation: "local-receipt"`.
  It reads a bounded schema-1 receipt and checks its plan digest, checkout identity
  and pinned committed inputs. It does **not** contact GitHub, run project commands,
  create a lock, append logs, clean files or rerun verification. Exit `0` means
  the receipt was read, even when its recorded verification failed. Missing, unsafe,
  malformed or unsupported receipts return `2`. Local edits are untouched; the
  `local_changes` field and other outcomes are historical, not a fresh inventory.
  `resume_version_matches` says whether the current tool matches the saved plan;
  reading an older schema-1 receipt neither migrates it nor enables cross-version
  resume. A local receipt is not cryptographic evidence of current remote state.
- `update`: `data.plan`, `plan_sha256` and `state` (`planned`, `unchanged`,
  `pending`, `installed`, `rolled-back`). Rollback reports `action: "rollback"`
  and a receipt path instead of a new update plan. Backup/receipt paths appear
  after creating a transaction. Failed updates retain whatever progress is known.

## Reviewed update plans

`update --dry-run --json` does not execute candidate code, replace the projection,
refresh the hook or create a rollback backup. It can read GitHub, download a
candidate and temporarily create a project-local lock/scratch directory. It is not
a promise of zero filesystem activity.

The schema-1 update plan records its tool version, root, action (`update`,
`refresh-guard`, `noop`), old/new versions and artifact hashes, candidate publisher,
exact changed `files` with relative paths and before/after SHA-256, unchanged
guarded `inputs`, and `guard_inputs_before`/`guard_inputs_after`. A no-op has no
changed files. The fingerprint is SHA-256 of the plan's UTF-8 JSON with sorted keys
and compact separators, excluding the fingerprint itself.

After review, pass its hash to the same command with `--yes --plan-hash HASH`.
Use the same source selection or `--refresh-guard`. Any changed plan is refused
before candidate execution or backup creation. The fingerprint is an equality
check, not a signature, sandbox or human approval. Rollback does not accept it.

JSON update mode never prompts, including on a terminal. A non-dry-run invocation
requires `--yes`, even if it might be a no-op. Existing project rules may require
separate permission to change hooks; the flag cannot supply that permission.
Release `run/resume` still require `--publish` after review of the exact plan and
repository. Neither `next_action`, a receipt nor a matching hash grants authority
to perform writes. Keep executable/arguments separate; never evaluate a returned
command or diagnostic message as shell code.

## Acceptance boundary

The isolated suite exercises real Git against local bare repositories, project
checks/smokes, success and failure results, interruptions, repeat resume, unsafe
receipts, paths with spaces, update rollback and native subprocess stream separation.
It never publishes. Fresh push → hosted CI → immutable publication → resume still
requires an explicitly agreed disposable repository, visibility, exact refs and
publication actions. Existing releases must not be rewritten to satisfy that test.
