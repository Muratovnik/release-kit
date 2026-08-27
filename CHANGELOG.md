# Changelog

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
