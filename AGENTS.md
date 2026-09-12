# release-kit repository instructions

This is an independent Git repository for one publishable tool. These instructions
are complete for a task started at this root; do not rely on a parent directory to
supply ownership, safety, verification, or commit rules. Locate this repository's
own Git root before staging or running checks. Its placement on a workstation does
not change ownership; never stage it as an unintended gitlink in another repository.

## What this repository is for

One publication gate other repositories adopt, plus focused diagnostics, curated
release notes, an optional release coordinator and a separately installed MCP adapter.
The gate composes maintained engines for secrets and links with the policy and
overlay facts only an adopting repository can declare. Start reader onboarding with
the standalone CLI; hooks, owner policy, release delivery and MCP are optional paths.

## The rule that outranks the others

This tool has to work for anyone, so nothing tracked here may hardcode one user's
world: not a project it guards, not a service it expects to find, not a board, a
host, or a path outside this repository. Everything specific belongs in the adopting
repository's configuration. Test fixtures use invented names (`Someservice`,
`example.invalid`) and placeholder users.

One case is absolute: no list of forbidden names may ever be committed here. That is
the file that would defeat its own purpose, since a tool shipping such a list
publishes precisely what it exists to keep out of published trees.

**This is not anonymity, and must not drift into it.** The maintainer's name and
email belong on every commit, in the LICENSE and in the package metadata, the same as
in any other repository, and a repository that names its own author or its own
sibling projects is doing something ordinary. What the gate is for is material that
*cannot travel*: a path that resolves on exactly one machine, a name a project has
declared it cannot publish yet. Confusing the two produces vaguer documents and no
security, and it costs the reader a concrete reference in exchange for nothing.

## Design constraints

- Every required release capability must have a path without paid services or a
  mandatory hosting account. Preparation, checks and release files are portable;
  hosting publication is an explicit adapter. Hosted CI and paid provenance are
  optional choices, never prerequisites inferred from a failed quota or payment.
  Do not change repository visibility or billing to make the default flow work.
- No Python runtime dependencies in the base CLI. This runs inside other repositories'
  hooks and CI, where a dependency tree is a reason not to adopt it. Betterleaks and
  Lychee are pinned external engines provisioned from verified official archives;
  do not reimplement their parsers or duplicate their pins in adopters. The optional
  MCP SDK decision is documented in docs/mcp.md; other dependencies need a decision.
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
- Distributed behaviour changes require a new version and dated changelog entry.
  Keep package and runtime versions aligned; the builder checks both and the
  updater refuses changed artifacts under the same version. Never replace an
  already-published release asset with different bytes, including packaged docs.

## Service-file ownership

- Keep service files, scratch space, diagnostics and backups inside the owning
  project by default, in managed ignored locations. A path outside it requires
  explicit user agreement on that exact path, including history backups.
- Record ownership before cleanup; never sweep unknown files, follow links or
  junctions, or silently claim another checkout's Git metadata. Preserve necessary
  recovery receipts and report retained files.
- Live release publication tests require a separately agreed disposable repository.
  The ordinary test suite must not publish or mutate a real hosted project.

## Verification commands

For CLI development use the canonical base runner, not a shortened list of commands:

```text
python tools/check.py
```

It runs stdlib unittest discovery, Ruff lint and Ruff format checks with the exact
source import path and a canonical temporary directory. This matters on hosts
where the system temporary path contains a link. The base suite deliberately
excludes optional SDK integration discovery; it cannot qualify the MCP adapter.

Before a joint CLI/plugin distribution, provision uv explicitly and run:

```text
python tools/check_distribution.py
```

This is the repository's configured release check. It runs the base gate, explicit
MCP discovery in the existing locked optional environment, a working-tree test
build, CLI smoke, real-engine onboarding and extracted-plugin stdio checks. Missing
requirements or empty/all-skipped MCP discovery fail. Tests never register clients,
install a real adopter's hook or publish. Retained evidence identifies actual host
and bytes, not an unexecuted OS matrix. Native-client discovery and hosted exposure
review remain separate. See CONTRIBUTING.md and docs/publication-review.md.

The publication audit is additional to code checks. From source in PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m releasekit.cli audit
```

Use `PYTHONPATH=src python -m releasekit.cli audit` in Bash. A history verdict needs
a committed clean tracked tree and all intended refs fetched. Never rewrite history
or publish merely to make a check pass.

Every change to a rule needs a test that fails without it. A regression that a real
repository hit gets a test that names what it hit, not a generic one. Keep release
notes user-facing; preserve historical entries and move detail to linked references.

## Git

After each coherent task-owned green boundary, make an incremental local commit
by default. Preserve unrelated work and pre-existing staged changes. Stage only
exact task-owned paths in one uninterrupted stage → inspect → commit sequence;
never leave a staged handoff. Never push unless explicitly requested.

Commit subjects are Conventional Commits with the Angular type set: `build`, `chore`,
`ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`; an
optional lowercase scope; `!` for a break. No machine authorship or vendor trailers.
