# Changelog

## [Unreleased]

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

## [Unreleased]
