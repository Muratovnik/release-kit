# Audit policy, scopes and private overlays

For a first check use the [minimal CLI setup](../README.md#first-check-in-an-existing-project).
The `audit` command composes project rules with Betterleaks secret scanning and
Lychee offline local-link checking. `exposure` and `overlay` are narrower diagnostic
commands, not substitutes for the combined publication gate.

The built-in text rules match concrete patterns, normalize selected text and apply
owner-declared exact values/regular expressions. They do not perform general
language understanding or upload a tree/private policy to an LLM. A passing result
establishes only the configured checks over the inspected scope.

## Configuration

`relkit.toml` belongs to the guarded repository. This is an **extended example**,
not a mandatory default; declare only rules meaningful for your project:

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

# Deliberate synthetic fixtures where structural rules are not meaningful.
# This does not suppress the secret scanner or non-excludable owner findings.
exclude = ["tests/fixtures/*"]

# Adoption debt can only shrink; a stale entry also fails.
[exposure.baseline]
"legacy/config.toml" = ["home-directory"]

# A public provider name is allowed only on declared product surfaces.
[exposure.providers.Someservice]
role = "product-data-provider"
allowed_surfaces = ["src/*", "docs/*", "tests/provider/*"]

# These declarations record review; they do not anonymize or generate files.
[exposure.provenance]
"tests/generated/*" = "synthetic"
"docs/screenshots/*" = "anonymized"
```

A provider's name is not private just because an owner uses its service. The
provider role vocabulary is closed: an owner-only workflow cannot be relabeled
as a product provider. Material declared `machine-derived` remains a finding and
must be replaced before publication; `anonymized` is the project's assertion that
review occurred, not an automatic anonymization guarantee.

The defaults enable secret/link checks. A normal `.betterleaks.toml` extends
maintained defaults:

```toml
title = "project secret scanning"
betterleaksMinVersion = "1.8.1"

[extend]
useDefault = true
```

Keep secret allowlists narrow, targeting a rule/path/value. Structural `exclude`
patterns are not secret-scanner allowlists. Baselines record existing adoption debt;
exclusions describe places where a structural rule is inappropriate. `--strict`
also fails on remaining baselines. Invalid or stale baseline entries do not create
a permanent green exception.

## Scopes

```text
python .github/relkit.pyz audit
python .github/relkit.pyz audit --staged
python .github/relkit.pyz audit --history
```

Worktree scope includes tracked files and untracked/unignored publication candidates.
Staged scope reads the Git index: `relkit.toml`, Betterleaks configuration and its
relative policy files must be indexed. Unstaged policy edits are not part of that
verdict. Betterleaks scans staged changes; policy and Markdown inspect the index.

History scope requires a clean **tracked** tree and complete local history. It
checks current `HEAD`, local/remote branches, tags, Git notes and fetched GitHub
pull, GitLab merge-request and Gerrit change refs. Ref names, annotated tag messages
and tagger identities are checked alongside commits and blobs. Synthetic client
checkpoint refs are deliberately outside this scope. Untracked/unignored files
can still be inspected by the worktree candidate pass; they are not magically
part of Git history. History rejects shallow clones, replace refs and nonempty
Git grafts, and disables replacement semantics in its Git/scanner processes.

Fetch host-only refs before a recovery review: local checks cannot inspect objects
the checkout does not possess. Neither Git history scanning nor its link checker
covers hosted issues, Actions logs or independently uploaded release files. See
[publication review](publication-review.md) for those separate surfaces.

CI normally uses `--history` without `--owner`; a public clone cannot know private
owner values. On the owner's reviewed installation use:

```text
python .github/relkit.pyz audit --history --owner
python .github/relkit.pyz audit --history --owner --require-overlay
```

Owner mode additionally requires the private policy and a valid installed guard.
`--require-overlay` also demands the exact mounted-link manifest. These flags are
not required for the [basic public-policy audit](../README.md#first-check-in-an-existing-project).

### Archives and external objects

ZIP-family containers are recognized by content in worktree, index and reachable
history: `.zip`, `.whl`, `.jar`, `.pyz`, Office/OpenDocument packages and
extensionless ZIPs. Unsafe entry paths/symlinks, malformed or over-budget containers,
private paths and matching text rules fail, including bounded nested ZIPs and
Unicode entries. Diagnostics identify the outer artifact and entry.

`forbid_png_metadata`, when enabled, also checks PNG entries and reachable historical
PNGs/archives. Its structural exclusions remain applicable.

Unsupported compressed formats (including tar/gzip, 7z and RAR) fail closed rather
than receiving a verdict over undecoded bytes. Malformed/over-budget declared ZIPs
are non-excludable inspection failures.

Git LFS pointers and submodule gitlinks fail because their external content is not
contained in the inspected Git blobs. Audit external repositories as separate roots;
review/migrate LFS material explicitly. Those failures and private owner-policy
findings cannot be accepted through a baseline or structural exclusion.

`--no-download` makes missing verified engine caches an operational failure. For
platform pins, cache integrity and explicit executable overrides, read
[distribution details](distribution.md#scanner-platform-and-cache-behavior).

## Private owner policy

Private topology, exact forbidden values, owner workflows and personal-data
expressions do not belong in public configuration. Owner mode discovers
`../<checkout-name>-private` by convention or the directory explicitly selected by
`RELKIT_PRIVATE_ROOT`. This root owns `.publication-private-values` and optionally
an `install.conf.yaml` link manifest. Exact values may include workspace names,
private namespaces, record identifiers and path fragments; do not publish the list.

The same root may contain `.publication-owner.toml`:

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

Pattern kinds are `owner-workflow`, `personal-data` and `machine-observation`.
Owner mode needs at least one exact value, workflow or pattern; an empty/missing
policy cannot produce a structural-only owner pass. Both owner-policy filenames
are unconditionally private if found in a public tree or archive.

Human authorship, license metadata and a project's own public identity are not
anonymity violations. The owner decides what cannot travel. A scanner cannot make
that decision solely from a name occurring in text.

## Repository guard

Install a guard only after reviewing its inputs and obtaining any separately
required permission to change a hook:

```text
python .github/relkit.pyz protect install --dry-run
python .github/relkit.pyz protect install --plan-hash REVIEWED
python .github/relkit.pyz protect check
```

The installed guard lives at `<git-common-dir>/hooks/pre-push` and runs the history
gate with mandatory private owner policy. It is not required for the basic CLI
installation. It pins the tracked projection, `relkit.toml`, the configured secret
policy and, for a workflow-backed release configuration, the publishing workflow.
Reviewed changes to pinned inputs require an explicit
[guard refresh](updates.md#already-updated-files-and-guard-drift).

If `core.hooksPath` redirects Git elsewhere, that external owner must first provide
an executable dispatcher with `# git-common-dir-hook-dispatcher: pre-push v1`.
Release-kit verifies compatibility before writing its repository guard; it never
creates/replaces the external dispatcher. The guard resolves `python`, `python3`
or `py`. Older supported guard templates keep enforcing their pins until explicitly
refreshed. For pre-0.5.0 ownership, follow the
[migration procedure](updates.md#migrating-the-guard-owner).

The local pre-push guard is not server-side branch protection. Do not bypass it or
weaken owner policy to make a release pass.

## Overlay contract

The private sibling's Dotbot `install.conf.yaml` is the single mount list.
Release-kit verifies that each `target: source` mount exists as a symlink or Windows
junction, resolves to the **exact** manifest source, is ignored/untracked publicly,
and points to a path tracked by the private repository. Merely resolving somewhere
inside the private root is not enough.

Dotbot creates links; release-kit verifies them. Projects with copy/sync overlays
may adopt the publication checks independently; changing the ownership/mount model
is a separate migration. `relkit overlay` diagnoses this contract without repairs.

## Diagnostic and automation interfaces

`relkit exposure` runs only built-in policy checks and remains for diagnosis and
compatibility. Use `audit` for combined release/CI checks. Release-note extraction
and validation are separate because they require a requested version; see
[notes](notes.md). Every command can opt into the
[structured CLI result](cli-json.md), whose exit/status semantics are part of the
public interface. A configuration or execution error is not evidence of safety.
