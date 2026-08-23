# release-kit repository instructions

This is an independent Git repository for one publishable tool. These instructions
are complete for a task started at this root; do not rely on a parent directory to
supply ownership, safety, verification, or commit rules. It sits inside another
repository's working tree for convenience only, is ignored by it, and must never be
staged there as a gitlink or a submodule.

## What this repository is for

Two gates other repositories adopt: an exposure check and a release-notes reader.
They are independent of each other and of every project that uses them.

## The rule that outranks the others

Nothing tracked here may name a project, a person, an organisation, a service, a
board, a host, or a path outside this repository. Not in code, not in tests, not in
documentation, not in a commit message. Everything specific to a user belongs in
their configuration.

This is not tidiness. The tool's whole purpose is to keep such material out of
repositories that publish, and a tool carrying a list of what must not be published
would leak exactly what it protects. Test fixtures use invented names
(`Someservice`, `example.invalid`) and placeholder users.

## Design constraints

- No runtime dependencies. This runs inside other repositories' pre-commit hooks and
  CI, where a dependency tree is a reason not to adopt it. The standard library is
  the budget; a new dependency needs a recorded decision.
- Every rule is an executable check with a test. A rule stated only in prose is not a
  control, which is the failure this repository exists to answer.
- False positives are the primary risk. A gate that cries wolf is switched off, and a
  switched-off gate is worse than none because it reads as coverage. Prefer resolving
  a path over matching its text; prefer a caller-supplied list over a guess.
- A check must be adoptable by a repository that already fails it, or it will not be
  adopted at all. That is what the baseline is for, and why a baseline entry that
  stops matching is itself a failure.
- Public behaviour is what the version protects: the `relkit` command line, its exit
  codes, the finding kinds, and the `relkit.toml` schema.

## Verification

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Every change to a rule needs a test that fails without it. A regression that a real
repository hit gets a test that names what it hit, not a generic one.

## Git

Commit after the tests pass. Stage exact paths, inspect the staged diff, and preserve
unrelated work. Never push unless asked.

Commit subjects are Conventional Commits with the Angular type set: `build`, `chore`,
`ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`; an
optional lowercase scope; `!` for a break. No machine authorship or vendor trailers.
