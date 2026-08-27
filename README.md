# release-kit

One publication gate for repositories with different languages and release systems:

```bash
python .github/relkit.pyz audit --history
```

The command owns the local verdict over the checked-out and fetched publication state:

- repository policy: private paths, forbidden file kinds, portable paths, ignored
  surfaces, optional PNG metadata and allowed Git identities;
- deterministic semantic policy for owner workflows, personal-data patterns,
  internal planning references, machine attribution and workstation observations;
- declared product-provider surfaces, fixture provenance, and ZIP-family publication
  artifacts, including reachable historical copies;
- secret detection by **Betterleaks 1.8.1** over the Git index or full history;
- offline local-link validation by **Lychee 0.24.2** over Git-owned Markdown;
- owner-only forbidden-name checks and exact private-overlay validation from an
  automatically discovered sibling private repository.

Betterleaks and Lychee remain the engines for domains they already solve. release-kit
owns their versions, official release URLs, SHA-256 digests, platform selection and
invocation. An adopting repository owns only `relkit.toml` and, where needed, a narrow
`.betterleaks.toml`. It does not carry download snippets or another secret/link parser.

## Distribution

Until the package has a public release channel, the source repository builds a
deterministic standard-library zipapp:

```bash
python tools/build_zipapp.py dist/relkit.pyz
```

Adopters track that projection at `.github/relkit.pyz`. The same file runs on Windows,
Linux and macOS with Python 3.11 or newer, and reports its release-kit and engine
versions with `--version`. Engines and their official archives are cached below the
guarded repository's ignored `.cache/release-kit/` directory. Every run verifies the
pinned archive digest and the cached executable against the archive before executing
it, then checks the reported version. Set `RELKIT_CACHE_DIR` to share a cache, or
`RELKIT_BETTERLEAKS` /
`RELKIT_LYCHEE` to point at pre-provisioned verified executables.

Once release-kit has a public package or Git release, projects may replace the tracked
projection with a digest-pinned download without changing their command or config.

## Configuration

`relkit.toml` lives in the guarded public repository:

```toml
[exposure]
private_paths = [".private", ".codex"]
private_files = ["AGENTS.local.md", ".mcp.json"]
private_suffixes = [".local.md"]
required_ignores = [".private", ".codex", "AGENTS.local.md", ".mcp.json"]
allowed_users = ["example-owner"]
allowed_identities = [
  "Example Maintainer <maintainer@example.invalid>",
  "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
]
forbid_png_metadata = true
forbid_ai_attribution = true
forbid_internal_planning = true
forbid_machine_observations = true
provenance_required = ["tests/generated/*", "docs/screenshots/*"]
betterleaks_config = ".betterleaks.toml"

# Deliberate fixtures where structural policy is not meaningful. This does not
# suppress Betterleaks, private owner values/workflows/patterns, Git LFS pointers, or
# submodule gitlinks; its secret exclusions stay in .betterleaks.toml and must be narrow.
exclude = ["tests/fixtures/*"]

# Adoption debt. It can only shrink: a stale entry is itself a failure.
[exposure.baseline]
"legacy/config.toml" = ["home-directory"]

# A provider name is public product vocabulary only on these surfaces. The role
# vocabulary is closed so an owner-only workflow cannot be disguised as a provider.
[exposure.providers.Someservice]
role = "product-data-provider"
allowed_surfaces = ["src/*", "docs/*", "tests/provider/*"]

# Declaring anonymized material is the required review confirmation. Anything
# declared machine-derived remains a finding and must be replaced before publication.
[exposure.provenance]
"tests/generated/*" = "synthetic"
"docs/screenshots/*" = "anonymized"

```

Private topology, owner workflows, and personal-data expressions are not valid public
configuration. Owner mode
discovers `../<checkout-name>-private` by convention, or the directory named by
`RELKIT_PRIVATE_ROOT`. That private root owns `.publication-private-values` and, when it uses a
link overlay, `install.conf.yaml`. The file contains exact private values: workspace
names, provider namespaces, record identifiers, and path fragments. A provider's public
name does not belong there merely because the owner's configuration uses that provider.

The same private root may carry `.publication-owner.toml` for typed semantic rules:

```toml
version = 1
owner_workflows = ["OwnerWorkbench"]

[[patterns]]
name = "private-record-reference"
kind = "personal-data"
expression = '''\brec_[0-9a-f]{12}\b'''

[[patterns]]
name = "local-inventory-observation"
kind = "machine-observation"
expression = '''(?i)observed on the maintainer's machine'''
```

Pattern kinds are limited to `owner-workflow`, `personal-data`, and
`machine-observation`. Owner mode requires at least one exact value, workflow, or
pattern; a missing or empty owner policy is never a structural-only green result.
The two owner-policy filenames are unconditionally private if they appear in a public
tree or archive.

The semantic checks are deterministic and local. release-kit does not upload the tree
or owner policy to an LLM. A repository can extend the structural rules with private
regular expressions without publishing those expressions or their matched values.

`.betterleaks.toml` normally only extends the maintained defaults:

```toml
title = "project secret scanning"
betterleaksMinVersion = "1.8.1"

[extend]
useDefault = true
```

Project allowlists belong there and should target one rule/path/value instead of
disabling a directory.

## Scopes

Use the same command at each lifecycle point:

```bash
# Worktree plus untracked/unignored publication candidates.
python .github/relkit.pyz audit

# Pre-commit: policy and Markdown read the Git index; Betterleaks scans staged changes.
python .github/relkit.pyz audit --staged

# Before a tag or first push: clean branch/tag history, current links and optional overlay.
python .github/relkit.pyz protect install
python .github/relkit.pyz audit --history --owner

# A project with mounted private files additionally requires their exact manifest links.
python .github/relkit.pyz audit --history --owner --require-overlay
```

CI runs `--history` without `--owner`: a clean public clone cannot know private names.
The public config still forbids and requires ignores for private surfaces. On the
owner's machine, `protect install` installs a managed pre-push hook that runs the
history gate with the mandatory private-value policy. The hook pins SHA-256 for the
tracked release-kit projection, `relkit.toml`, and the configured Betterleaks policy
before it executes repository-controlled code. A reviewed change to any of those
inputs requires another `protect install`. `--require-overlay` adds the exact
link/target/tracking oracle. History mode refuses a dirty worktree; use `--staged`
while preparing a commit, then run `--history` against the committed release candidate.
Current `HEAD`, local and remote branches, tags, Git notes, and locally fetched GitHub
pull, GitLab merge-request, or Gerrit change refs are publication history. Public ref
names, annotated tag messages, and tagger identities are checked alongside commits and
blobs; synthetic client checkpoint refs are deliberately outside that scope. Host-only
refs must be fetched before a recovery audit because a local repository cannot inspect
objects it does not have. The owner pre-push guard prevents new unreviewed commits from
reaching those refs; repository-host branch protection remains the server-side trust
boundary. History mode refuses shallow repositories, replace refs, and non-empty Git
grafts, and disables replacement semantics in its own Git and Betterleaks processes.

ZIP containers are recognized by content and inspected as publication surfaces in the
worktree, index, and reachable history. This includes Office/OpenDocument packages and
extensionless ZIP artifacts as well as `.zip`, `.whl`, `.jar`, and `.pyz`. Unsafe paths
and symlinks, invalid or over-budget declared archives, private paths, and semantic
findings inside Unicode entries or bounded nested ZIP-family archives fail with the
outer artifact and entry named in the diagnostic.

Unsupported compressed archives such as tar/gzip, 7z, and RAR fail closed instead of
receiving a semantic verdict over bytes the built-in inspector cannot decode. Invalid
or over-budget declared ZIP containers are likewise non-excludable inspection
failures.

Git LFS pointers and submodule gitlinks fail closed: their external objects are not in
the Git blobs this repository can prove it inspected. Audit an external repository as
its own release-kit root, and replace or explicitly migrate LFS material before a
publication verdict. These findings and private owner-policy findings cannot be
accepted through a baseline or hidden by a structural exclusion.

`--no-download` turns missing cached engines into an operational error, useful in
sealed environments. Caches created before 0.4.1 need one online audit to retain the
verified release archives used for subsequent offline integrity checks. `--strict`
also fails on adoption baselines.

## Overlay contract

The private sibling's Dotbot manifest is the single mount list. release-kit reads its `target: source`
entries and verifies that every mount:

- exists and is a symlink or Windows junction;
- resolves to the exact manifest source, not merely somewhere under the private root;
- is ignored and untracked by the public repository;
- points at a path tracked by the private repository.

Dotbot still creates links. release-kit verifies them and supplies the release verdict.
Repositories that currently use a copy/sync overlay can adopt the publication checks
independently; migrating that ownership model is a separate change.

## Other commands

`relkit exposure` runs only the built-in policy ratchet. It remains for diagnosis and
backward compatibility; release and CI gates should use `relkit audit`.

`relkit overlay` diagnoses only the configured link overlay.

`relkit notes v1.2.0 --output notes.md` extracts the human-curated changelog entry for
a release. It does not regenerate release notes from commits.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
python -m ruff check src tests tools
```
