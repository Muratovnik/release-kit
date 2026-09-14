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
committed entry is what `relkit notes` extracts and what a release publishes.

Three properties make the output valid, and editing the template must keep all three:

- the heading compares two versions and carries a date: `## [1.2.0](…/compare/v1.1.0...v1.2.0) (2026-01-01)`;
- section names come from the bounded set the profile accepts;
- the first line of every bullet carries a link whose label is the commit it points at.

`relkit notes --draft` refuses a draft that loses any of them, which is the reason to
generate through it rather than around it.

Already using Node? The layout is what `conventional-changelog -p angular` produces, so
the most faithful source is that tool itself:

```toml
[changelog.generator]
command = ["npx", "conventional-changelog", "-p", "angular"]
```
