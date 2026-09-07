# Changelog

## [Unreleased]

## [0.17.0] - 2026-09-07

### Added

- `relkit update --prune-backups` removes update backups that no receipt can
  restore. One receipt names one backup, so every other directory was already
  unreachable through `--rollback` and simply accumulated. It removes only
  directories with this updater's exact naming, inside the repository's own Git
  directory, not the one the current receipt names, and holding nothing but the
  files this updater writes; anything else is reported and left alone. It takes no
  update source, refuses while an interrupted update still needs its backup,
  previews with `--dry-run` and confirms like any other write. A completed update
  now also reports how many such backups remain.

### Fixed

- `relkit update` no longer leaves a scratch directory behind on every run,
  including a dry run. The workspace confines the XDG directories for its
  children, so `gh` wrote its own device id beside the download; because only
  files inventoried before a consumer runs may be removed, cleanup correctly
  refused to sweep it and retained the directory. The updater now adopts that one
  known subtree after the download, and everything else still stays retained and
  reported.

## [0.16.0] - 2026-09-07

### Added

- Publication verification now reads GitHub's signed release attestation
  (`in-toto.io/attestation/release/v0.2`) through `gh release verify` and requires
  it to agree with the REST asset list: the same names, the same SHA-256 digests,
  no extra or missing asset, the release database id and repository id of this
  plan, and the annotated tag object this run pushed. The asset set that decides
  a release is therefore a claim GitHub signed, not an API response that a
  rewritten answer could choose. Immutable releases were already mandatory and the
  documented `gh` floor already exceeds the 2.81.0 that introduced the command, so
  no adopter requirement changed.

## [0.15.0] - 2026-09-06

### Fixed

- The pre-push guard resolves an interpreter instead of assuming `python` is on
  PATH. It tries `python`, `python3` and `py`, rejects a Windows Store alias by
  running each candidate, and says which runtime is missing rather than failing
  the push with a bare shell error. An installed guard from an earlier release is
  still recognized as intact and keeps enforcing its pins; `relkit protect check`
  reports the older template and `protect install` adopts the current one.
- `relkit update --rollback` accepts the guard the receipt says the update wrote.
  The previous check recomputed the guard, so any release that changed the guarded
  input list or the template refused to undo itself. Receipts now pin the exact
  installed bytes as `new_guard_sha256`, and an intact guard pinning the installed
  artifact is accepted for receipts written before this version.
- `relkit audit --history` blocks on a tracked difference rather than on any
  untracked file. An untracked file is scanned by the worktree pass and cannot
  reach a remote, and refusing every push over an unrelated scratch file was the
  fastest route to `--no-verify`. The refusal now names the offending entries.
- Release planning compares only the stable `vX.Y.Z` tags between local and
  remote, and names the ones that disagree. A personal local tag or a pre-release
  candidate no longer refuses to plan a release.
- The MCP adapter refuses only the Git variables that redirect the repository
  (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`, `GIT_NAMESPACE`
  and the object-directory pair), not the whole `GIT_*` namespace. A Git that
  refuses to identify the checkout is reported as a tool error instead of an
  unhandled `CalledProcessError`.
- A reviewed MCP project binding stamps the configured release workflow, so a
  change to the file that publishes expires the binding like a policy change.
- A truncated PNG is reported as a truncated chunk stream rather than as a named
  metadata chunk; the finding kind is unchanged.

### Changed

- Waiting for CI polls the workflow run with a backoff from 5 to 30 seconds and
  reconciles the full remote state on entry, on completion and about once a
  minute, instead of reconciling on every poll. An hour-long wait no longer costs
  thousands of GitHub API requests.
- History auditing reads Git objects in batches bounded by bytes rather than by a
  fixed count of 128, so a repository with large blobs in its history stops
  spiking memory and timing out. The release source snapshot uses the same
  batched read instead of one `git cat-file` process per file, and planning
  resolves the previous tag and the changelog's commit references with a handful
  of Git processes instead of two or three per tag and per link.
- Engine child processes get the same scratch and cache confinement as every
  other child (`XDG_*` as well as `TMP`/`TEMP`/`TMPDIR`).

## [0.14.0] - 2026-09-06

### Added

- `relkit release abandon vX.Y.Z --reason "..."` records that an unpublished
  attempt ends: the remote tag must already be gone and no release or draft may
  exist. The receipt keeps its history with the recorded outcome, `resume` then
  refuses the attempt, and the next `run` for the same version archives the old
  receipt beside the new one instead of refusing. No ref is ever deleted.
- Planning reads the committed tag workflow's block-style `jobs:` mapping and
  refuses a `required_jobs` entry that no job id or literal name declares, before
  any tag exists. Matrix labels match by their prefix before ` (`; expression
  names and flow-style mappings are reported as unverifiable rather than guessed,
  and jobs outside `required_jobs` are listed as optional.
- `release plan` prints the exact asset set and the plan's caveats as a separate
  operator block: the set is verified only after the immutable release exists, a
  checksum manifest must list every other asset, and a green local run is not the
  CI platform matrix. `data.plan.caveats`, `workflow_jobs` and `host` carry the
  same facts in JSON.

### Fixed

- The owner pre-push guard also pins the configured release workflow once the
  `[release]` coordinator is configured, so an upload step that changes the
  published file set needs the same review as `relkit.toml`. Existing guards
  report the new input as drift until refreshed.
- A busy release lock reports its recorded PID, tag and root together with
  whether that PID is still running, instead of only the lock path.

## [0.13.2] - 2026-09-05

### Fixed

- Staged audits read the indexed publication policy and Betterleaks configuration,
  including relative policy files, so unrelated working-tree edits cannot change
  the verdict for a prepared commit.
- MCP project bindings expire when the configured Betterleaks policy changes,
  including projects that use a custom policy filename.
- The PNG metadata policy also checks images inside nested ZIP-family artifacts
  and reachable historical copies, while preserving explicit structural exclusions.
- Source and plugin packages include the MIT license and human maintainer metadata.
  The standalone zipapp carries the full license in its existing build metadata,
  preserving compatibility with earlier project updaters.

## [0.13.1] - 2026-09-04

### Fixed

- Vue-like notes now require commit evidence on the opening source line of each
  top-level change bullet, preventing renderers from showing hashes as detached
  rows. Release guidance keeps adopter-internal release tools and their versions
  out of product-facing notes unless they materially affect product users.

## [0.13.0] - 2026-09-03

### Added

- MCP guard installation, release run and release resume accept existing direct
  user authorization with separate scopes and reviewed project/operation hashes.
  `resume_plan` previews the saved release and selected resume options without
  contacting the publisher. Native confirmation remains available when authority
  is absent; an update request never grants publication permission.

### Fixed

- Required release guards are checked during planning and before project commands,
  then rechecked after commands for drift. Dirty history audits stop before scans
  or engine provisioning, retaining the existing failure exit code.
- The release skill defaults to the existing checkout and distinguishes optional
  local test isolation from disposable hosted tests of release-kit itself. It
  reviews prerequisites before long checks and keeps coordinator-owned checks in
  the managed run rather than manually repeating the release sequence.

## [0.12.1] - 2026-09-02

### Fixed

- Windows engine selection now queries the operating system if Python reports
  an empty machine type, including plugin clients without processor environment
  variables. Verified-scanner audits and transactional updates no longer fail
  with an empty architecture. Unsupported architectures still refuse; no engine
  verification, project authorization or rollback checks are bypassed.

## [0.12.0] - 2026-09-02

### Added

- Project binding and bundled sync accept the client's attestation of an existing
  direct user request, scoped to checks, update or rollback and a fresh review
  hash. An explicitly requested update needs no second consent dialog. Responses
  identify the authorization source; project/policy/plan drift still refuses.

### Compatibility

- Native confirmation remains the fallback and is still required for other
  writes, including publication. Update authority never changes client security
  settings, authorizes unrelated changes or overrides a subsequent human refusal.
  Transactional updater checks, owned-hook handling and backups are unchanged.

## [0.11.0] - 2026-09-02

### Added

- Plugin-owned `relkit_sync` status, plan, apply and rollback for an explicit
  project. The verified bundled CLI is the update target and executor; previews
  do not run the older project projection or require a project-code binding.
  Writes retain exact-plan confirmation, backups and the existing updater gates.
- A single release-set builder exports the exact bundled zipapp alongside the
  plugin and checksums. Project alignment reports both versions and hashes;
  installing a plugin does not silently change a repository or downgrade it.

### Fixed

- Confirmation declines and cancellations now return actionable structured
  errors without attributing a client-policy refusal to the human. Neither
  outcome authorizes a CLI fallback or an approval-policy change.

## [0.10.0] - 2026-09-02

### Added

- One installable plugin containing the release workflow skill, full MCP adapter
  and a locked, isolated optional runtime. Plugin packages are built from the same
  versioned sources as the CLI; project projections are not upgraded implicitly.
- Explicit, human-confirmed project bindings for plugin mode. Every operation
  names its binding; no current-directory inference or mutable default project.
  Bindings expire on restart or reviewed-input drift, and writes still require
  their own exact-operation confirmation.

### Compatibility

- The standalone operator-pinned MCP interface and dependency-free zipapp remain
  available. Plugin mode accepts existing project projections from 0.9.0 onward.

## [0.9.0] - 2026-09-02

### Added

- Optional project-bound MCP stdio adapter for every CLI workflow, including
  confirmed release run/resume, update/rollback, guard installation and notes export.
  The official MCP SDK is an isolated optional extra; the zipapp stays dependency-free.
- Dry-run plans and stale-plan rejection for rollback and guard installation;
  structured release receipts now include the complete validated saved plan.

### Safety

- MCP writes require client-mediated human confirmation of exact reviewed inputs.
  Projection drift requires a server restart with a reviewed pin. Tools cannot
  select arbitrary projects, executables or shell arguments.
- Hook writes reject linked or external targets and use exclusively created
  temporary files without overwriting an unrelated partial file.

## [0.8.0] - 2026-09-02

### Added

- Opt-in JSON result schema for all commands, with semantic findings, errors,
  release stages and continuation arguments; progress and engine output use stderr.
- Read-only `release status` for saved run receipts, explicitly distinguished
  from a fresh remote verification and compatible with older schema-1 receipts.
- Structured update dry-run plans with exact artifact, guarded-input and hook
  digests; optional `--plan-hash` rejects changed plans before candidate execution.
  JSON updates require explicit `--yes` and never prompt interactively.

### Compatibility

- Default text output and exit meanings remain available. No MCP server or new
  runtime dependencies. Existing projections and published releases stay pinned.
- Resume still requires the release-kit version that created the saved plan;
  reading an older receipt does not migrate it or authorize publication.

## [0.7.1] - 2026-09-02

### Fixed

- Preserve literal square brackets in committed source snapshot filenames,
  including dynamic web routes. Exact Git blob names are not glob patterns;
  configured paths, traversal, unsupported files and portability remain guarded.

## [0.7.0] - 2026-09-02

### Added

- Opt-in experimental `release plan/run/resume` for GitHub tag-driven CI: pinned
  source/ref plans, explicit push permission, annotated tags, narrow atomic pushes,
  remote reconciliation, exact CI run/job checks and recoverable local receipts.
- Immutable publication verification of committed notes, exact asset sets, sizes,
  SHA-256 manifests, native release/build attestations and certificate-bound CI
  identity, followed by project-owned smoke commands from exact committed blobs.
- Explicit review of a newer attempt of the same CI run on resume, with publication,
  verification and cleanup reported independently. No automatic CI/release repairs.
- Project-owned service storage with conservative file-inventory cleanup and guards
  against escaping paths, links, junctions and hard-linked service files.

### Changed

- Updater downloads and audit snapshots now use project-local ignored scratch
  space. External cache writes require explicit approval of the exact cache path;
  existing verified external caches remain readable without provisioning writes.
- Runtime, package and zipapp identity advance together to 0.7.0. Existing adopter
  copies and published releases are unchanged until explicitly updated.

### Fixed

- Materialize staged symbolic links as their exact indexed text so publication
  engines cannot follow their targets outside the owned snapshot.

## [0.6.0] - 2026-09-01

### Added

- `relkit update` for one project's tracked zipapp: verified GitHub release assets
  or explicitly SHA-256-pinned local artifacts, dry-run plans, explicit confirmation,
  backups, validation, owned-guard refresh and automatic or explicit rollback.
- `relkit update --refresh-guard` for already-reviewed projection or policy changes,
  with per-input and guard checksums and the same confirmation/backup workflow.
- Deterministic distribution identity and checksum sidecars; packaging checks that
  runtime, package metadata and the dated changelog identify the same version.
- Explicit `strict` and `vue-like` changelog profiles for `relkit notes`, with a
  declared first version, source-line diagnostics, duplicate/empty-entry rejection
  and checks of dates, compare targets, sections and per-change commit links.
  Legacy extraction remains the default; `--strict` enables the baseline checks.
- A shared validate-and-extract API and consumer integration instructions that keep
  hand-edited highlights, grouped changes and breaking-change explanations intact.

### Fixed

- Preserve existing notes on validation or output-replacement failure, refuse an
  output alias of the changelog or policy, and report configuration/I/O errors.
- Export notes as UTF-8 on stdout as well as to files, including on Windows hosts
  whose default console encoding cannot represent the curated text.
- Guard-drift diagnostics identify each changed input, its pinned/current digest,
  and the explicit project-authorized repair command.

## [0.5.0] - 2026-08-30

### Changed

- `relkit protect` now owns only the repository's Git common-directory pre-push
  guard. When `core.hooksPath` redirects the effective hook, install verifies a
  neutral compatible dispatcher before changing the guard and never writes the
  external hook path.
- The source version is `0.5.0`. This is a breaking ownership change for the `0.x`
  line: `0.3.x` and `0.4.x` installers own the redirected dispatcher and must not be
  mixed with `0.5.0` guard refreshes during a rollout.

## [0.4.1] - 2026-08-27

### Fixed

- Detect real `User` home paths, UNC/WSL/Cygwin and root home forms, file URIs,
  UTF-16/UTF-32 and printable binary strings, Unicode compatibility forms, and
  invisible formatting characters in the worktree, archives, and reachable history.
- Match exact and case-variant private roots, nested owner-policy files, and
  case-variant private suffixes.
- Let provider declarations live in `relkit.toml` without allowing provider data
  outside declared product surfaces.
- Inspect every historical blob/path association, fetched pull/merge/change refs,
  Git notes, public ref names, annotated tag messages and taggers, repository-escaping
  Git symlinks, nested archives, archive symlinks, and invalid archive artifacts.
- Fail closed on unreadable candidates and convert scanner or engine timeouts into
  stable operational errors.
- Fail closed on Git LFS pointers and submodule gitlinks whose external contents are
  not part of the audited repository.
- Reject embedded Git bundle files as unaudited history containers.
- Reject shallow history and local replace/graft views, and disable Git replacement
  semantics for both built-in history checks and Betterleaks.
- Keep private owner values, workflows, and patterns active inside paths excluded as
  deliberate public-rule fixtures.
- Prevent baselines and exclusions from accepting private owner findings, Git LFS
  pointers, or submodule gitlinks.
- Verify cached engine executables against their pinned official archives before
  executing them.

### Changed

- The owner pre-push guard pins the release-kit projection, public policy, and secret
  engine configuration. Reviewed changes to those inputs require `protect install`.
- ZIP containers are recognized by content, including Office/OpenDocument packages
  and extensionless artifacts.
- Unsupported compressed containers and invalid/over-budget ZIP artifacts now fail
  closed and cannot be hidden by exclusions or baselines.
- Exact private surfaces and the Betterleaks configuration must use portable paths
  contained by the guarded repository, without Windows aliases or alternate streams.
- `include_candidates` now controls both structural and Betterleaks worktree scopes.
- Structural policy, Betterleaks, and Lychee now share an index-based worktree
  snapshot that preserves sparse-checkout entries while applying real deletions and
  untracked candidates.

## [0.4.0] - 2026-08-24

### Added

- Typed private owner policy for owner workflows and deterministic personal-data or
  workstation-observation patterns.
- Opt-in checks for internal planning markers, machine attribution, and observations
  derived from an owner's workstation across files, commit messages, and history.
- Public product-provider surface contracts and provenance declarations for fixtures
  and screenshots.
- Bounded inspection of ZIP, WHL, JAR, and PYZ contents in the current tree and
  reachable history.

### Changed

- Owner policy files are unconditionally private publication surfaces.
- Semantic findings now carry non-sensitive explanations while preserving stable
  finding kinds for baselines.

## [0.3.2] - 2026-08-24

### Fixed

- Detect declared private values after harmless whitespace wrapping, including
  values split across lines in reachable history.

## [0.3.1] - 2026-08-24

### Fixed

- Commit-message history checks now apply only privacy rules; prose describing
  a traversal fixture no longer fails the publication gate.

## [0.3.0] - 2026-08-24

### Added

- Mandatory owner mode that discovers private values from the sibling private
  repository and refuses a missing or empty policy.
- A managed pre-push guard installed and verified by `relkit protect`.
- Private-value checks for Git paths and commit messages, case-insensitively.

### Changed

- Private-root, manifest, and private-value locations are no longer valid public
  `relkit.toml` configuration. The conventional sibling or `RELKIT_PRIVATE_ROOT`
  owns those facts.

## [0.2.1] - 2026-08-24

### Added

- `relkit audit`, the single publication verdict composing repository policy,
  Betterleaks 1.8.1, Lychee 0.24.2, Git history and private-overlay verification.
- Verified automatic engine provisioning and a deterministic cross-platform zipapp
  projection for repositories and CI.

### Changed

- Markdown parsing is delegated to Lychee instead of release-kit's former regular
  expression parser.
- Overlay verification now requires the exact manifest target and rejects publicly
  tracked mounts.

All notable user-facing changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
[Semantic Versioning](https://semver.org/).

What a version protects: the `relkit` command line, its exit codes, the finding kinds
it reports, and the `relkit.toml` schema.

This file starts at the first published version.
