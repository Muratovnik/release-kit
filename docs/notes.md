# Release notes

`relkit notes v1.2.0 --output notes.md` extracts an entry from the project's changelog.
Review and commit it before publication: the release coordinator publishes the
committed entry, including those edits. Standalone extraction reads the working file
and does not verify its Git state. With a project projection, replace `relkit` with
`python .github/relkit.pyz`.

`relkit notes v1.2.0 --draft` produces an entry with the project's configured generator
and holds it to the same profile a published entry must satisfy. Release-kit does not
insert it into the changelog; you review the draft, edit it and commit it. A declared
custom generator is project code and runs with the operator's permissions.

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

The profile defines a bounded commit-linked layout, based on
[conventional-changelog](https://github.com/conventional-changelog/conventional-changelog).
Its heading and section rules are listed below. The starter template emits this
layout; an existing generator may need its own writer configuration.

## Generators

```toml
[changelog.generator]
engine = "git-cliff"
```

| Form | Meaning |
| --- | --- |
| `engine` | A tool release-kit provisions and verifies, exactly like the scanners: one official archive, its pinned SHA-256, one executable checked against its own pinned digest. Supported: `git-cliff`. |
| `command` | An exact argv this project supplies, run as written from the project root. Use it for anything else, including the Node tools. |

The git-cliff adapter sends its output to stdout even when `cliff.toml` or the
environment names an output file. It disables external commands in template
preprocessors and postprocessors, and ignores `GIT_CLIFF_PREPEND`. The `command`
form runs its declared argv exactly; review that command's own file and network effects.
Both forms stop ordinary descendant processes when the generator exits or times out.

For a project that already owns a Node generation script, declare that script's exact
argv, for example:

```toml
[changelog.generator]
command = ["npm", "run", "--silent", "draft-release-notes"]
```

The custom command receives no injected version or range arguments. Its script or
project metadata must select the requested version and release boundary, print the
entry to stdout, and produce the configured layout. Check the installed generator's
output options: the current conventional-changelog CLI requires
[`--stdout`](https://conventional-changelog.js.org/conventional-changelog/cli/)
for a preview. Its stock
[`angular` preset](https://conventional-changelog.js.org/presets/angular/)
uses a level-one heading for a non-patch release and can add sections for breaking
`refactor`, `docs` and other types. Adapt those writer choices to this profile, or use
`strict` when the project's own layout is intentional.

`git-cliff` reads its own `cliff.toml` from the project root, so the template stays
yours. A starter that satisfies the profile is in
[examples/changelog](../examples/changelog/cliff.toml). Whatever the generator emits,
`--draft` refuses it unless it satisfies the configured profile.

By default, git-cliff drafts the changes after the latest local tag. To select the
release's actual predecessor, pass `--from-tag` with `--draft`:

```text
relkit notes 1.2.0-rc.2 --draft --from-tag v1.2.0-rc.1
relkit notes 1.2.0 --draft --from-tag v1.1.0
```

Use the predecessor reported by `release next` or the release plan. The final example
includes changes made before any intervening `1.2.0-rc.*` tags. Release-kit verifies
that the exact local tag exists, has an unambiguous name and is reachable from `HEAD`,
then asks git-cliff to
treat the whole range as one entry. This overrides tag-selection, skip-tag and
ignore-tag settings for that invocation; commit filters and the template still belong
to the project. It does not establish which tags were published. `--from-tag` is
supported only by the maintained git-cliff adapter; a custom command supplies its own
range in its declared argv.

A generated bullet is a commit subject, which is written for a reviewer rather than for
a reader of the release. Put what a reader needs in a `Highlights` section: it is
exempt from the per-bullet commit link, so hand-written context sits above the
generated list without breaking the layout.

### Drafting without remote metadata

git-cliff can fetch remote metadata when a remote is configured. To generate from
local Git commits, enable its [offline mode](https://git-cliff.org/docs/configuration/remote/#offline)
for that invocation. In a POSIX shell:

```bash
GIT_CLIFF_OFFLINE=true relkit notes 1.2.0 --draft --from-tag v1.1.0 \
  --output notes-1.2.0.md
```

The starter still renders compare and commit URLs from its configured owner and
repository and the local history. Pull-request titles, labels and other remote
metadata are unavailable; templates depending on that enrichment may need online
generation. See git-cliff's [offline limitations](https://git-cliff.org/docs/tips-and-tricks/#handling-remote-git-service-api-rate-limits).

Release-kit may still provision the pinned git-cliff executable if it is missing.
For a fully offline run, the verified executable must already be in release-kit's
cache.

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
version. A version with the same major, minor and patch numbers as the explicitly
declared `first_version` may omit comparison: its heading may be unlinked or point
to `/releases/tag/<current>`. This covers initial release candidates and the stable
final in either order: `first_version = "0.1.0"` permits `0.1.0-rc.1`, and
`first_version = "0.1.0-rc.1"` permits `0.1.0`. Another version core still requires a
comparison. A truncated changelog is not proof of a first release; Git and the
coordinator establish the actual publication boundary.

Allowed third-level sections are `Highlights`, `Features`, `Bug Fixes`,
`Performance Improvements`, `Reverts`, `BREAKING CHANGES` and `Breaking Changes`.
Empty or unknown sections fail.

### Headings in other languages

Declare heading aliases to use the same profile with a Russian changelog, or with
any other language. Aliases identify a section's meaning without translating or
rewriting its text:

```toml
[changelog]
profile = "conventional-changelog"

[changelog.section_aliases]
"Главное" = "highlights"
"Новое" = "features"
"Исправлено" = "fixes"
"Производительность" = "performance"
"Отменённые изменения" = "reverts"
"Несовместимые изменения" = "breaking"
```

| Meaning | Default English headings | Validation |
| --- | --- | --- |
| `highlights` | `Highlights` | Editorial prose and bullets |
| `features` | `Features` | Top-level bullets with commit links |
| `fixes` | `Bug Fixes` | Top-level bullets with commit links |
| `performance` | `Performance Improvements` | Top-level bullets with commit links |
| `reverts` | `Reverts` | Top-level bullets with commit links |
| `breaking` | `BREAKING CHANGES`, `Breaking Changes` | Migration prose and bullets |

The existing English headings remain accepted, including historical entries. You
can declare several aliases for one meaning. Names are exact, case-sensitive,
nonempty single lines without surrounding whitespace; the six meaning identifiers
in the table are fixed. An alias cannot change the meaning of an English heading.
Use the same displayed names in your generator's groups. This setting changes
changelog validation; CLI messages and configuration keys retain their existing names.

### Change content

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

`releasekit.release.changelog.entry_for(text, version, profile="conventional-changelog", first_version="0.1.0", section_aliases={"Исправлено": "fixes"})`
returns the original entry or `None` when absent. Invalid entries raise
`ChangelogError` with a one-based `line`. No Git checkout or network is required.
