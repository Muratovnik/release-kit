# Releases without a hosting dependency

Preparation uses Git, the project's build/check/smoke commands and ordinary files.
It needs no hosting account, API token, hosted runner or paid provenance service.
The default delivery is a local directory. A hosting adapter is a separate choice.
GitHub delivery and the older GitHub Actions workflow remain supported; a GitLab
publishing adapter is not implemented. The prepared files and directory manifest
can be consumed by another delivery tool without changing the build.

## Portable configuration

```toml
[changelog]
profile = "strict"
first_version = "1.0.0"

[release]
publisher = "directory"
directory = ".cache/releases"
version_file = "VERSION"
version_pattern = '^([^\s]+)$'
assets = ["application.zip", "SHA256SUMS"]
checksum_file = "SHA256SUMS"
checks = [["python", "tools/check.py"]]
build = [["python", "tools/build.py", "--output", "{assets}"]]
smoke = [["python", "tools/smoke.py", "--assets", "{assets}"]]
smoke_platforms = ["linux", "darwin", "win32"]
```

These are project-owned commands, not programs supplied by release-kit. Declare
only platforms where that project's smoke can run. Build receives a nonexistent
output directory and must create the exact configured asset set there. It runs
from a snapshot of the committed source; it cannot rely on an implicit Git tag,
ignored dependencies or uncommitted files in that snapshot. Provision dependencies
explicitly in project commands when required. Checks execute in the source checkout.

The destination must be inside the project and ignored by Git. The default is
`.cache/releases`; add `.cache/` to `.gitignore` before preparation. Neither a
remote nor `gh` is needed for directory delivery. Omit `repository` and `branch`.
An absent publisher selects directory delivery unless a legacy workflow was
explicitly configured; old workflow configurations keep their original publisher.

## Releases without files

A library that is consumed from its Git tag, such as a skill collection or a
configuration set, has nothing to package. Declare an empty asset set:

```toml
[release]
publisher = "github"
repository = "example/skills"
version_file = "VERSION"
version_pattern = '^([^\s]+)$'
assets = []
checks = [["{python}", "tools/check.py", "--all"]]
smoke = [["{python}", "tools/check.py"]]
smoke_platforms = ["linux", "darwin", "win32"]
```

`build` and `checksum_file` must then be absent: there is no file set to produce
or to list. Everything else is unchanged. The release is the annotated tag, the
committed changelog entry and the source tree at that tag. Smoke runs from the
exact Git-free snapshot of that tree, so it proves the tree works without a
checkout, ignored files or a build step; `{assets}` names an empty directory.
The candidate receipt records an empty file set, directory delivery writes the
manifest and an empty `assets/`, and GitHub delivery creates and publishes the
release without an upload. Verification refuses a file that appears later in
either destination.

For GitHub delivery the immutable-release check binds only the tag object, since
the signed attestation lists no assets. That path is covered by the isolated
fixtures; no live publication of a release without files has been recorded here.

## Commands

```bash
relkit release next --bump patch
relkit release prepare 1.0.0
relkit release plan 1.0.0
relkit release run 1.0.0 --publish --plan-hash REVIEWED
relkit release status 1.0.0
relkit release verify 1.0.0
```

Prepare runs checks, publication audits, build and smoke before creating a release
tag. Its receipt binds source SHA, settings, version and all file sizes/digests.
Each failed preparation retains its attempt and can retry the same version.
Review the resulting plan before run. Run rechecks current local gates and the
prepared bytes, creates the annotated local tag and publishes those exact bytes.
It does not rebuild at publication time. Use `resume VERSION --publish` after an
interruption; it reconciles the saved destination before advancing.

### Prereleases and the final release

Target projects can publish [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html)
versions, including `1.0.0-rc`, `1.0.0-rc.2` and `1.0.0-rc.2+build.17`.
The optional `v` prefix is tag syntax; the recorded version has no prefix.
`version_pattern` extracts the complete committed version, including any suffix;
release-kit then validates its SemVer syntax and ordering. A pattern restricted
to three numbers must be updated before preparing an RC.

To select a numbered RC, use:

```bash
relkit release next --bump patch --prerelease rc
relkit release prepare 1.0.0-rc.1
relkit release plan 1.0.0-rc.1
relkit release run 1.0.0-rc.1 --publish --plan-hash REVIEWED
```

This example assumes `first_version = "1.0.0"` and no stable publication yet.
Commit `VERSION` and its changelog entry for the selected version before prepare.
After a stable publication, `--bump` chooses the next stable core first; the
prerelease label then selects `rc.1` or the next number above a published `rc.N`
of that core. The label must be one nonnumeric ASCII identifier, such as `alpha`,
`beta` or `rc`. Use an explicit version for other SemVer identifier layouts.
An occupied unpublished RC is reported as occupied and is not skipped. Choosing
an earlier channel after a later one was published is refused.

An RC's predecessor is the latest published prerelease with the same `X.Y.Z`,
or the previous stable release if there is no RC for that core. A final release
uses the previous stable publication, keeping all changes shipped in its RCs in
the final notes. Published prereleases for other version cores do not advance
this boundary. The first declared core admits both its initial RCs and its final
release; declaring `first_version = "1.0.0-rc.1"` also permits the eventual `1.0.0`.

When generating notes, pass the `previous.tag` from `release next` or the plan
explicitly as `notes --draft --from-tag TAG`; the newest Git tag may be an RC or
an abandoned candidate. For example, after stable `0.9.0` and RCs of `1.0.0`,
`relkit notes 1.0.0 --draft --from-tag v0.9.0` drafts the complete stable range.
Review, update and commit the changelog before planning again.

Build metadata remains part of the exact version, tag and asset identity but
does not increase SemVer precedence. A change from `1.0.0-rc.2+build.1` to
`1.0.0-rc.2+build.2` cannot substitute for a new release number. GitHub delivery
marks RC releases as prereleases and verifies that status during recovery;
directory delivery records the same status from the manifest's version.
The release-kit CLI's own wheel, plugin and updater retain their documented
stable-version distribution contract; target-project SemVer support does not
convert arbitrary SemVer identifiers to Python package versions.

### One invocation

```bash
relkit release plan 1.0.0
relkit release run 1.0.0 --publish --prepare --plan-hash REVIEWED
```

`run --prepare` prepares the candidate and publishes it in one process, so the
checks, both audits, the build and the smoke run once. A check result is never
reused across invocations, because a commit does not identify the dependencies,
tools, hooks or owner policy a later process runs under; inside one invocation
nothing runs between the gate and the tag, so that rule is kept without a second
pass. The hash to review is the one `plan` prints before a candidate exists; the
candidate is bound to the plan after preparation, and its receipt is written as
usual. The run receipt records `preparation.invocation = "same"`. A `resume`
after an interruption is a new invocation and runs the gate again before the tag.
The Actions adapter refuses the flag; its candidate comes from CI.

Directory delivery writes `VERSION/assets/` and `VERSION/relkit-release.json`
below the destination, staging the complete set before a directory rename.
The portable manifest contains version, source SHA, tag object, notes and digests;
it contains no workstation path or credentials. Published directories are never
overwritten. Their files remain ordinary filesystem files: verification detects
changes, but this is not server-enforced immutability or a signed attestation.
Retain these manifests and Git tags together when moving the release history.
Next derives its version from published manifests, not an uncompleted tag.
Publication receipts from a different destination do not redefine this history.

Candidate receipts and files are retained under
`.git/relkit/candidates/VERSION/ATTEMPT/` for recovery. Successful publication does
not erase the prepared source of a retry. Inspect a reported partial export stage
before removing it; release-kit never overwrites unknown output to recover.

## Optional GitHub delivery

Use `publisher = "github"` and `repository = "example/project"` instead of
directory delivery. Keep the same build/check/smoke commands. An optional `branch`
can be pushed along with the tag. This adapter uses native `gh` release commands
and the Releases API, not Actions, Actions artifact storage or hosted minutes.
Use release-kit 0.21.1 or newer for GitHub delivery: unpublished drafts must be
resolved through the authenticated releases list, because the tag endpoint only
returns published releases.
It requires GitHub CLI >= 2.98.0, repository write access and admin read access
for the immutable-release preflight. Enable release immutability before planning.
Visibility and billing settings are never changed by release-kit.

The adapter creates its own marked draft, uploads only missing matching members,
compares the complete draft against the prepared files, then publishes it. It
never clobbers an uploaded file. Saved intents and draft identity let resume
reconcile lost create/upload/publish responses. Foreign drafts, changed notes,
unexpected assets and identity drift cause refusal. Published downloads, GitHub's
signed immutable-release statement and application smoke are checked afterwards.
GitHub may produce its signature after publishing; if verification is not ready,
repeat verify without republishing.

Disable any existing tag-triggered publisher before selecting this adapter; two
independent publishers must not race on the same tag. Hosted checks can stay
manual and optional. CI is reported as `not-required`, never falsely as passed.
There is no automatic downgrade from required CI or provenance after a failure.

`publisher = "github-actions"` selects the older
[Actions contract](release-coordinator.md), including its declared jobs and
signature obligations. Actions quotas and account policy may block that optional
mode. Configuring a self-hosted runner is another operator-owned option, not a
prerequisite introduced by release-kit.

The native capabilities and cost boundary are documented by
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[gh release create](https://cli.github.com/manual/gh_release_create) and
[gh release upload](https://cli.github.com/manual/gh_release_upload).
