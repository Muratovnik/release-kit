# Changelog generator starter

A `cliff.toml` that makes [git-cliff](https://git-cliff.org) emit the layout the
`conventional-changelog` profile validates. Copy it to your project root, change
`[remote.github]` to your own owner and repository, and declare the generator:

```toml
[changelog]
profile = "conventional-changelog"
first_version = "0.1.0"

[changelog.generator]
engine = "git-cliff"
```

Then draft an entry for the version you are about to release:

```text
relkit notes 1.2.0 --draft
```

Nothing is written into `CHANGELOG.md`. Review the draft, add a `Highlights` section if
the commit subjects do not say what a reader needs, paste it in and commit it. The
reviewed entry remains in the changelog for `relkit notes` to extract. The release
coordinator publishes the committed entry.

For the declared first release, the starter emits its release-tag link. For later
releases it emits a comparison. If local tags include release candidates or
unpublished versions, select the predecessor from the release plan explicitly:

```text
relkit notes 1.2.0 --draft --from-tag v1.1.0
```

This includes the whole range after `v1.1.0`, including changes already listed in
intermediate release-candidate tags. The git-cliff adapter forces stdout and disables
external template commands, so configured output/prepend destinations cannot overwrite
the changelog during drafting.

Three properties make the output valid, and editing the template must keep all three:

- the heading carries a date and compares two versions, or uses the declared first-release exception: `## [1.2.0](…/compare/v1.1.0...v1.2.0) (2026-01-01)`;
- section names come from the bounded set the profile accepts;
- the first line of every bullet carries a link whose label is the commit it points at.

The starter includes exact `feat`, `fix`, `perf` and `revert` types, case-insensitively,
and breaking changes of any Conventional Commit type. Breaking changes get their own
section, with the migration explanation from `BREAKING CHANGE:` or `BREAKING-CHANGE:`
when supplied. A different
type such as `feature` does not become a `feat` entry by sharing its prefix.

For headings in Russian or another language, change the group names in `cliff.toml`
and declare their meanings in `relkit.toml`, for example:

```toml
[changelog.section_aliases]
"Новое" = "features"
"Исправлено" = "fixes"
"Несовместимые изменения" = "breaking"
```

Existing English entries continue to work. See [heading aliases](../../docs/notes.md#headings-in-other-languages)
for all section meanings and validation rules.

`relkit notes --draft` refuses a draft that loses any of them, which is the reason to
generate through it rather than around it.

For an existing Node generator, declare the project's command and adapt its writer
to the selected profile. See [custom generators](../../docs/notes.md#generators) for
the stdout, version, range and layout contract; a stock Angular preset can emit a
different heading level or additional sections.
