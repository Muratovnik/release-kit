# release-kit

Two gates any repository can adopt, neither of which knows anything about yours.

- **`relkit exposure`** — fails when tracked files carry material that belongs to the
  machine they were written on: an absolute home directory, a relative path that
  climbs out of the repository, a file kind no repository should publish, or a name
  the project declared off-limits.
- **`relkit notes`** — prints the changelog entry a repository already wrote for a
  version, for use as release notes.

They are independent. A project that publishes nothing still wants the first; a
project with nothing private still wants the second. A release pipeline may run the
exposure gate before it tags, which is the only order that helps: the tag is what
triggers publication, so a check that runs after it reports on something already
published.

## Install

```bash
pip install release-kit
```

## Exposure

Add `relkit.toml` to the repository being guarded:

```toml
[exposure]
# Names that must not appear, one per line in this file. Keep it out of the
# repository's own history: a list of what must not be published cannot itself
# be published. Absent, only the structural rules run - which is what a clone
# should do.
names_file = ".publication-names"

# Paths the rules do not apply to: a test fixture that has to contain the very
# thing a rule detects. Not a baseline, and not a place to put debt.
exclude = ["tests/*"]

# What was already there when the gate was adopted. The gate fails on anything
# new, and on an entry that stops matching, so this can only shrink.
[exposure.baseline]
".someclient/settings.json" = ["home-directory", "escapes-repository"]
```

`exclude` and `baseline` answer different questions and must not be swapped. A
baseline entry says *this is debt and it will go*; the gate makes it impossible to
forget, because clearing the finding without clearing the record is a failure. An
exclusion says *the rules were never meaningful here*, and nothing will ever make it
go. Using an exclusion for debt hollows out the gate while it still reports success.

Then run it from the repository's own gate, its pre-commit hook, and CI:

```bash
relkit exposure
relkit exposure --strict   # also fails on the baseline, to check it has been cleared
```

Finding kinds are `home-directory`, `escapes-repository`, `forbidden-kind` and
`declared-name`.

The baseline exists because a gate that is red on the day it is adopted is a gate
somebody turns off. Recording what is already there stops the bleeding immediately
and leaves the debt visible; fixing a finding forces the record to be updated in the
same change, because a record that no longer matches is itself a failure.

## Notes

```bash
relkit notes v1.2.0                          # print the entry
relkit notes v1.2.0 --output notes.md        # write it, for gh release create --notes-file
```

It reads; it does not generate. Generating the entry from the commit log is a
changelog tool's job and belongs before the commit. Re-rendering at publish time
would publish the uncurated text and throw away the editing pass that makes a
changelog worth reading, and it would let a release describe a version the
repository never wrote down.

An entry is found by its `## [version]` heading, linked or not, and runs to the next
heading. A tag and a heading may differ by a leading `v`.

## Design rules

- Nothing here names a project, a person, a service, a board, or a path outside the
  repository it is pointed at. Everything specific is configuration.
- Every rule is an executable check. A rule written only in a checklist is not a
  control; that is the failure this exists to answer.
- False positives are the real enemy. A relative path that climbs and comes back
  inside the repository is ordinary documentation, so paths are resolved rather than
  matched, and only a result outside the root is reported.
- No runtime dependencies. This runs inside other repositories' pre-commit hooks.

## Development

```bash
python -m unittest discover -s tests -p "test_*.py"
```
