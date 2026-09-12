# Curated release notes

`relkit notes v1.2.0 --output notes.md` extracts the changelog entry a maintainer
already wrote. It does not regenerate notes from commits. With a project projection,
replace `relkit` with `python .github/relkit.pyz`.

The command reads `[changelog]` from the root's `relkit.toml`. Relative `--changelog`
and `--output` paths are based on that root; `--root` selects another root.

## Validation profiles

| Profile | Guarantee |
| --- | --- |
| `legacy` (default) | Original extraction, including heading-only entries and the first duplicate; also used without `relkit.toml` |
| `strict` | Reject empty entries and duplicate headings for the requested version; leave layout to the project |
| `vue-like` | Strict checks plus the bounded layout and visible commit-link rules below |

`notes --strict` enables strict validation without configuration and never weakens
an existing `vue-like` profile. A malformed policy is an error, not a fallback.
Opt in explicitly; `cliff.toml` or generated-looking text does not enable a profile.

```toml
[changelog]
profile = "vue-like"
first_version = "0.1.0"
```

The [Vue changelog](https://github.com/vuejs/core/blob/main/CHANGELOG.md) is the
layout reference. [git-cliff](https://git-cliff.org/docs/templating/examples/) can
prepare a draft before human review; release-kit never runs it or requires equality
with generated output.

## Vue-like layout

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

`releasekit.release.changelog.entry_for(text, version, profile="vue-like", first_version="0.1.0")`
returns the original entry or `None` when absent. Invalid entries raise
`ChangelogError` with a one-based `line`. No Git checkout or network is required.
