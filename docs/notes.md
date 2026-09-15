# Release notes

`relkit notes v1.2.0 --output notes.md` extracts the changelog entry that is committed
to the project. That entry is what gets published, always: a generator can draft it,
but nothing publishes text nobody reviewed. With a project projection, replace `relkit`
with `python .github/relkit.pyz`.

`relkit notes v1.2.0 --draft` produces an entry with the project's configured generator
and holds it to the same profile a published entry must satisfy. It writes nothing into
the changelog and authorizes nothing; you review the draft, edit it and commit it.

The command reads `[changelog]` from the root's `relkit.toml`. Relative `--changelog`
and `--output` paths are based on that root; `--root` selects another root.

## Validation profiles

| Profile | Guarantee |
| --- | --- |
| `legacy` (default) | Original extraction, including heading-only entries and the first duplicate; also used without `relkit.toml` |
| `strict` | Reject empty entries and duplicate headings for the requested version; leave layout to the project |
| `conventional-changelog` | Strict checks plus the bounded layout and visible commit-link rules below |

`notes --strict` enables strict validation without configuration and never weakens
an existing `conventional-changelog` profile. A malformed policy is an error, not a fallback.
Opt in explicitly; `cliff.toml` or generated-looking text does not enable a profile.

`conventional-changelog` was called `vue-like` before 0.25.0. The former name is still
accepted and validates identically; `audit` reports it and names the current one. Change
it at your convenience. Refusing it instead, as 0.25.0 and 0.26.0 did, left a project no
way to move: the installed CLI could not read the new name and the candidate could not
read the old one, so `update` failed its own post-update audit and rolled back.

```toml
[changelog]
profile = "conventional-changelog"
first_version = "0.1.0"
```

The layout is the one [conventional-changelog](https://github.com/conventional-changelog/conventional-changelog)
emits with its `angular` preset, which is what the
[Vue changelog](https://github.com/vuejs/core/blob/main/CHANGELOG.md) is generated with.

## Generators

```toml
[changelog.generator]
engine = "git-cliff"
```

| Form | Meaning |
| --- | --- |
| `engine` | A tool release-kit provisions and verifies, exactly like the scanners: one official archive, its pinned SHA-256, one executable checked against its own pinned digest. Supported: `git-cliff`. |
| `command` | An exact argv this project supplies, run as written from the project root. Use it for anything else, including the Node tools. |

Only a provisioned tool may be named instead of spelled out. A short name for a
third-party command would have to guess at the environment behind it — `npx` or a
global install, which package manager, which version — and this tool does not guess.

For a project that already has Node, the most faithful way to reproduce that layout is
the tool that defines it:

```toml
[changelog.generator]
command = ["npx", "conventional-changelog", "-p", "angular"]
```

`git-cliff` reads its own `cliff.toml` from the project root, so the template stays
yours. A starter that satisfies the profile is in
[examples/changelog](../examples/changelog/cliff.toml). Whatever the generator emits,
`--draft` refuses it unless it satisfies the configured profile.

A generated bullet is a commit subject, which is written for a reviewer rather than for
a reader of the release. Put what a reader needs in a `Highlights` section: it is
exempt from the per-bullet commit link, so hand-written context sits above the
generated list without breaking the layout.

## conventional-changelog layout

Use a level-two version/date heading, for example:

```markdown
## [1.2.0](https://example.invalid/compare/v1.1.0...v1.2.0) (2026-01-05)

### Bug Fixes

- Correct the example output ([abc1234](https://example.invalid/commit/abc1234567)).
```

The version is SemVer, including prereleases/build metadata; a leading `v` is
optional. The date must be real. The HTTP(S) compare URL ends in
`/compare/<previous>...<current>`, naming a different predecessor and the requested
version. Only explicitly declared `first_version` may omit comparison: its heading
may be unlinked or point to `/releases/tag/<current>`. A truncated changelog is not
proof of a first release; this declaration itself does not verify Git tags.

Allowed third-level sections are `Highlights`, `Features`, `Bug Fixes`,
`Performance Improvements`, `Reverts`, `BREAKING CHANGES` and `Breaking Changes`.
Empty or unknown sections fail.

Ordinary sections contain top-level `-`, `*` or `+` bullets. Each needs an inline
Markdown commit link on its opening source line: a 7–64 hexadecimal label matching
the beginning of the hash in an HTTP(S) `/commit/<hash>` URL. Scope and PR links are
optional; a PR link alone is insufficient. Related commits may share a bullet.
A continuation-line link or nested detail does not supply its parent's evidence.

Highlights and breaking-change sections permit prose, migration examples and
bullets without commit links. Comments, code blocks and inline code do not count.
This is a bounded layout, not a general Markdown parser: use inline links,
top-level sections/bullets and at most three spaces for wrapped prose. Arbitrary
HTML and reference-style links are not substitutes for the supported syntax.

Only the requested entry is validated. An empty `Unreleased` section or older
entries do not prevent incremental adoption. These checks establish structure and
visible traceability, **not completeness, factual accuracy, commit existence or
release membership**. Maintainer review and coordinator Git checks own those.

## Export safely

```bash
python .github/relkit.pyz notes "$TAG" --output release-notes.md &&
  gh release create "$TAG" --notes-file release-notes.md
```

The second command publishes and requires separate publication authorization.
Validation/extraction alone does not authorize it. Commit the reviewed opt-in
configuration and curated entry, then replace any duplicate consumer notes parser.
Keep consumer tag/source checks and the publication audit. `audit` cannot implicitly
validate notes because it has no exact requested release version. Protected
consumers also need the [guard refresh](updates.md#already-updated-files-and-guard-drift)
for reviewed policy/artifact changes.

Exit `0` means successful export; `1` means absent/invalid entry, with file, line and
reason; `2` means configuration/I/O failure. Validation failure creates/overwrites
no file. `--output` writes a temporary file before replacement, refuses the source
changelog/policy, and does not create parent directories. Prefer it to shell
redirection, which truncates an existing destination before validation can fail.

Output is UTF-8. Strict profiles preserve entry content and internal line endings,
trim trailing blank line endings and append one final LF. Publication never
regenerates or reformats human edits.

## Python API

`releasekit.release.changelog.entry_for(text, version, profile="conventional-changelog", first_version="0.1.0")`
returns the original entry or `None` when absent. Invalid entries raise
`ChangelogError` with a one-based `line`. No Git checkout or network is required.
