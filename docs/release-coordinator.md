# Managed GitHub tag releases

Status: experimental; covered by isolated Git/API fixtures, not yet accepted by a
live publication in a user-approved disposable repository.

## Ownership and prerequisites

`relkit release` is optional. Existing `audit`, `notes`, `protect` and `update`
remain independent. The coordinator owns ordering, Git/ref checks, observation,
verification and its local receipts. **The project's tag-triggered GitHub Actions
workflow is the sole publisher.** release-kit never creates/edits a GitHub release,
uploads assets, reruns CI, commits changes or repairs a tag.

The first version supports github.com, stable `vX.Y.Z` tags and a regular checkout
with its `.git` directory inside the project. It requires complete history, matching
local/remote tags, ordinary portable source files (no links/submodules), an active
workflow and GitHub CLI 2.98.0 or newer. `gh` is an explicitly provisioned native
integration, not an automatically installed runtime dependency.

Use the existing checkout by default. An additional local clone is an optional
project-specific way to isolate checks from active development, not a release
requirement. Configured commands run in the selected checkout and may modify
ignored build output; choose that checkout and verify its prerequisites before
long checks. A disposable hosted repository is for testing this coordinator's
publication machinery, not a prerequisite for every ordinary project release.

Before adoption, configure the CI publisher to:

- publish only from the intended tag, after its own independent checks;
- attach exactly the configured files, including any checksum manifest, while the
  release is a draft, then publish an **immutable** release;
- use the selected committed changelog entry as its release body;
- produce GitHub build-provenance attestations for **every configured asset**, from
  this same workflow and commit, with the tag as source ref;
- expose the configured required job names in the selected workflow run.

The coordinator does not enable repository settings, change CI or grant permissions.
The operator must review this integration before allowing a push. A missing
attestation or incorrect publication can be detected **after publication**; this is
not a server-side transaction or a promise that a bad release can be unpublished.
Reusable signer workflows, alternative signature schemes, prereleases, automatic
build dispatch and multi-platform local execution are not supported by this version.
Artifacts for every platform are verified; application smoke runs only on the
current explicitly declared host platform.

## Configuration

Add an opt-in section to the adopting project's committed `relkit.toml`:

```toml
[changelog]
profile = "vue-like"
first_version = "1.0.0"

[release]
repository = "example/project"
remote = "origin"
workflow = ".github/workflows/release.yml"
required_jobs = ["publish"]
version_file = "VERSION"
version_pattern = '^([0-9]+\.[0-9]+\.[0-9]+)$'
changelog = "CHANGELOG.md"
assets = ["example-{version}.zip", "SHA256SUMS"]
checksum_file = "SHA256SUMS"
checks = [["python", "tools/check.py"]]
smoke = [["python", "tools/smoke.py", "--assets", "{assets}"]]
smoke_platforms = ["linux", "darwin", "win32"]
timeout = 1800
command_timeout = 600
require_guard = false
owner_audit = false
# Optional: also push this existing branch, which must point to the release SHA.
# branch = "main"
```

Commands are argv arrays, not implicitly interpreted shell strings. Supported
placeholders are `{python}` (the current interpreter), `{source}`, `{assets}`,
`{temp}`, `{version}`, `{tag}` and `{commit}`. Escape literal braces as `{{`/`}}`.
Do not put credentials in configuration or argv. Build language, archive layout,
platform coverage and downloaded-application behavior belong to these project
commands. Checks run in the current clean checkout; smoke runs in a fresh snapshot
of exact Git blobs, without checkout filters, archive export attributes, `.git`,
ignored dependencies or later local edits. Scripts must provision any needed
dependencies locally and use `{temp}` for scratch output. `{assets}` is populated
only for smoke, not for pre-release checks. Executing project scripts is an explicit
trust boundary, not an operating-system sandbox.

`version_pattern` must have exactly one capture and match exactly one requested
version. Asset templates support only `{version}` and `{tag}` and must expand to
distinct portable filenames. `checksum_file` is optional: GitHub SHA-256 asset
digests are always checked; a declared manifest additionally must contain exactly
one standard `SHA256  filename` or `SHA256 *filename` line for every other asset.

`require_guard = true` verifies the existing owned guard and compatible dispatcher
during planning, before project commands and again afterward to detect drift;
it does not install or refresh either. `owner_audit = true` additionally requires
the existing private owner policy and `require_guard = true`. These options do not
grant permission to alter project hooks or override its instructions.

## Plan, run, resume

```bash
relkit release plan v1.0.0
relkit release run v1.0.0 --publish --plan-hash REVIEWED_SHA256
relkit release resume v1.0.0 --publish
# After manually reviewing a CI rerun of the SAME run, explicitly accept its attempt:
relkit release resume v1.0.0 --publish --accept-ci-attempt 2
```

For a vendored projection, replace `relkit` with `python .github/relkit.pyz`.
`--root` selects the owning project. `--no-download` prevents audit-engine downloads,
not the release asset downloads needed for verification.

`plan` performs read-only Git and GitHub queries, outputs the exact source SHA,
previous tag/object, notes, asset names, workflow identity, refspecs and fingerprint.
It does not run project commands, create service files, tag, push or publish.
`--publish` on `plan` still cannot publish. A stale `--plan-hash` stops execution.
Without a supplied hash, `run --publish` authorizes its freshly computed plan.

`run` requires a clean repository including untracked candidates. It runs each
declared check once, the worktree and history audits, and configured guard checks.
It creates an annotated tag, checks the newly introduced tag metadata and pushes
only the planned exact refs with Git's atomic push. Existing pre-push hooks still
run; their independent trust boundary is not bypassed to eliminate repeated scans.
Tag signing follows the user's existing Git configuration. No force push, automatic
stash, history rewrite, blanket ref push, global Git change or external-hook edit
is performed. History backups are neither created nor needed by this workflow.

The saved receipt binds the plan, repository ID, SHA, tag object, CI run/attempt,
release ID, asset IDs/sizes/digests and separate publication/verification/cleanup
results. A repository-wide exclusive lock prevents concurrent runs; it is never
stolen automatically. After a killed process, inspect the PID and exact lock path
reported in the error, and remove only that stale lock after proving it is inactive.

`resume` first checks actual remote identity/refs/release, including after a lost
push response. It never adopts an unrelated tag/release or silently substitutes
another successful CI run. A partial draft stays owned by CI: resolve its CI failure
then resume, rather than creating a duplicate. A newer attempt of the same run needs
the explicit flag above; its artifact attestations must also identify that attempt.
Missing/deleted/rewritten objects require owner investigation, not automatic repair.
Keep the same release-kit version and checkout for an unfinished run.

Local checks are not cached across invocations: a commit SHA does not identify
ignored dependencies, environment, tool binaries or hooks. After a verified push,
new local edits are left alone and reported separately; they do not become release
inputs. A repeated resume re-verifies publication without pushing or publishing
again. It can therefore detect later edits to notes, which GitHub still allows on
immutable releases.

## Evidence and boundaries

Git, not the changelog generator, determines the predecessor and commit membership.
Loose and packed tags have identical semantics. Local/remote tag disagreement fails
closed; no implicit fetch or `pack-refs` repair takes place. The first version must
be explicitly declared, never inferred from an empty generator result. A subsequent
heading must compare the actual previous tag to the current tag in this repository.
Commit links outside editorial sections are checked for existence, matching label,
repository, reachability and exclusion from the previous release. The Vue-like
profile additionally requires per-change links; legacy/strict profiles cannot
prove provenance for prose that supplies none. Standalone `notes` stays Git-free.

CI selection checks workflow ID/path, repository ID, push event, tag name, exact SHA,
creation time and run attempt, then every required job. A same-named branch is
refused. The REST run's branch-name field alone cannot prove a tag; final verification
also uses native `gh attestation verify` with full `refs/tags/...`, source SHA and
signer workflow/SHA. Its verified **certificate extensions**, not the workflow's
editable predicate, must identify the repository ID, push trigger and exact CI
run/attempt URL. Native `gh release verify` and `verify-asset` validate the immutable
release's signature and asset membership. Every downloaded file is size/hash checked
before smoke; metadata and file digests are checked again afterward.

The maintained integration is based on the official
[workflow-run API](https://docs.github.com/en/rest/actions/workflow-runs),
[release-asset API](https://docs.github.com/en/rest/releases/assets),
[attestation verification](https://cli.github.com/manual/gh_attestation_verify),
[release verification](https://cli.github.com/manual/gh_release_verify) and
[immutable-release contract](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).
The certificate field spelling is defined by
[sigstore-go](https://github.com/sigstore/sigstore-go/blob/v1.3.0/pkg/fulcio/certificate/extensions.go);
the invocation URL is constructed by the
[GitHub certificate identity provider](https://github.com/sigstore/fulcio/blob/main/pkg/identity/github/principal.go).
GitHub CLI 2.98.0 routes its TUF cache through
[the CLI-owned cache directory](https://github.com/cli/cli/blob/v2.98.0/pkg/cmd/attestation/verification/tuf.go).
release-kit sets process-local cache/temp paths, not user-wide GitHub settings.

## Service-file policy and cleanup

All release-kit service files default to locations **inside the owning project**.
An external location requires explicit user agreement on that exact path; an
inherited environment variable or a blanket approval is not enough. This applies
equally to diagnostics, downloads and any separately requested history backup.

Normal checkouts use `.git/relkit/tmp/` for owned scratch space and
`.git/relkit/releases/vX.Y.Z/` for receipts/logs. These locations are automatically
outside the tracked tree. Audit snapshots in linked worktrees use the checkout's
ignored `.cache/release-kit/`, never the other checkout's Git directory; the command
refuses if that local fallback is not ignored. The release coordinator itself and
updater do not support external Git metadata.

The updater keeps its compatible `.git/relkit-update.json` receipt and recorded
`.git/relkit-update-*/` rollback backup; downloads now stay in project-local scratch
space. Rollback backups are intentional retained recovery data, not throwaway
history copies. No sibling/parent backup directory is created.

Engine caches default to ignored `.cache/release-kit/`. Reading an already complete,
verified external cache or explicitly provisioned executable remains supported.
Writing an external cache now requires both `RELKIT_CACHE_DIR` and the same exact
absolute path in `RELKIT_APPROVED_EXTERNAL_CACHE`, set only after the user's approval.
Unset the override to use the project default. release-kit does not change these
user-wide variables itself.

Only inventoried, unchanged files are deleted. Unknown/changed files, symlinks,
junctions and hard-linked service paths cannot be swept as generic scratch data.
Success removes disposable downloads and snapshots but keeps the compact receipt
and detailed log required for repeat verification. Errors retain those diagnostics
and any data that cannot safely be removed; the command reports the directory.
Project-produced unknown scratch files are deliberately retained rather than
claimed retroactively. Project commands should clean their own outputs. Logs may
contain private command output and must be reviewed before sharing.

Exit codes: `0` published and verified (cleanup can separately report retained
files); `1` a recorded run failed a check; `2` invalid request/preconditions;
`3` waiting/transport timeout or a draft requiring continuation. The ordinary
output gives stage, result, reason, diagnostics and the resume command. No live
publication test is authorized by running the isolated test suite.

`release status vX.Y.Z` reads the saved receipt without changing files or checking
GitHub. Its exit `0` means the read succeeded, not that the published release is
currently valid. All commands accept the opt-in [structured CLI contract](cli-json.md);
recorded publication, verification and cleanup remain separate fields.
