# release-kit

Check Git repositories before publishing them. release-kit combines secret scanning,
local Markdown link checks and project-defined rules for files and Git history.
It can also prepare release files, deliver them to a directory or GitHub, and
verify what was published.

Start with the standalone CLI. Release coordination, private owner policy, Git
hooks and the [MCP plugin](docs/plugin.md) are optional, independent additions.
The base CLI is a single Python zipapp; it adds no Python dependencies to your app.

## Requirements and limits

| Use | Requirements |
| --- | --- |
| CLI | Python 3.11+ and Git on PATH (`python` below means your Python 3.11+ interpreter) |
| Full audit | Pinned Betterleaks and Lychee executables; the first audit downloads and verifies them |
| Full audit platforms | Linux x64/arm64 (GNU builds), macOS x64/arm64, Windows x64 |
| GitHub delivery or updates | GitHub CLI; delivery needs `gh` 2.98.0+, authentication and the documented repository permissions |
| Plugin | Python 3.11+, `uv`, a writable installation directory and a local client supporting this plugin's stdio configuration |

The Python CLI and the external scanners have different platform requirements.
There is no pinned native Windows arm64 Lychee asset. Unknown/unpinned scanner
platforms refuse rather than select an unverified binary. See
[distribution and platform details](docs/distribution.md).

The audit is not a general security assessment or proof that a project is safe to
publish. Private names must be declared by their owner. Built-in text rules use
specific patterns, not general language understanding. The audit does not inspect
GitHub issues, Actions logs or un-fetched history. LFS pointers, submodules and
unsupported compressed archives fail the relevant inspection. Linked worktrees
are not supported by the updater, release coordinator or MCP adapter. Read the
[scope and exclusions](docs/audit.md#scopes) before adopting the gate.

## First check in an existing project

Work from the root of the Git repository you want to check. No hook, private
sibling, MCP client or hosting account is needed for this path.

### 1. Obtain and verify the CLI

Download `relkit.pyz` and `relkit.pyz.sha256` from the **same reviewed release** on
[GitHub Releases](https://github.com/Muratovnik/release-kit/releases). Do not run
an unverified download. The following example uses the already-published
`v0.21.1`, not unpublished `main`:

```text
gh release download v0.21.1 --repo Muratovnik/release-kit --pattern relkit.pyz --pattern relkit.pyz.sha256 --dir .cache/relkit-download
```

`gh` is only the download method in this example: downloading those two files
through the release page works too. A private repository requires authorized
access. Choose an empty download directory; do not overwrite another installation.

**PowerShell**, from the project root:

```powershell
$expected = ((Get-Content .cache/relkit-download/relkit.pyz.sha256 -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash .cache/relkit-download/relkit.pyz -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -notmatch '^[0-9a-f]{64}$' -or $actual -ne $expected) { throw 'CLI checksum mismatch' }
if (Test-Path .github/relkit.pyz) { throw 'Already installed: use the update guide' }
New-Item -ItemType Directory -Force .github | Out-Null
Copy-Item .cache/relkit-download/relkit.pyz .github/relkit.pyz
```

**Bash**, from the project root:

```bash
(cd .cache/relkit-download && shasum -a 256 -c relkit.pyz.sha256) &&
  test ! -e .github/relkit.pyz &&
  mkdir -p .github &&
  cp .cache/relkit-download/relkit.pyz .github/relkit.pyz
```

The sidecar detects mismatched/corrupted bytes; a hash downloaded beside a file is
not independent publisher authentication. Trust the release source and, where
available, verify its immutable release with `gh release verify` and
`gh release verify-asset`. See [trust boundaries](docs/distribution.md#integrity-and-trust).

### 2. Add the minimal configuration

Create `relkit.toml` (merge deliberately if the file already exists):

```toml
[exposure]
betterleaks_config = ".betterleaks.toml"
required_ignores = [".cache"]
```

Create `.betterleaks.toml`:

```toml
title = "project secret scanning"
betterleaksMinVersion = "1.8.1"

[extend]
useDefault = true
```

Add `.cache/` to the project's `.gitignore` **before running the audit**. Keep any
existing ignore rules. The downloads and verified scanner cache belong there.
These exact starter files also live in [examples/audit](examples/audit/README.md).
No scanner is disabled in this example.

### 3. Run and interpret the result

```text
python .github/relkit.pyz --version
python .github/relkit.pyz audit
```

The first audit may download the pinned scanner archives. It checks tracked files
and untracked/unignored publication candidates, but does not stage, commit, install
a hook, tag, push or publish. It writes local caches and temporary inspection files.

`relkit audit: passed` with exit `0` means the configured checks passed for that
scope. Exit `1` means findings: read the paths and fix or review them. Exit `2`
means configuration or execution failed; it is not a clean security result.
For automation use `audit --json` and the [result contract](docs/cli-json.md).

Review and commit `.github/relkit.pyz`, `relkit.toml`, `.betterleaks.toml` and the
`.gitignore` change. Never commit the cache. After committing, check the complete
local history before publication:

```text
python .github/relkit.pyz audit --history
```

History needs a non-shallow clone and a clean tracked tree. It cannot inspect refs
that were never fetched. Existing historical findings may require a separate
owner-approved response; do not rewrite history just to obtain a pass.

## Choose the next capability

| Task | Guide |
| --- | --- |
| Configure private paths, baselines, owner rules or overlays | [Audit policy and scopes](docs/audit.md) |
| Update a project's pinned CLI, refresh its guard or roll back | [Updates and recovery](docs/updates.md) |
| Validate and export curated release notes | [Release notes](docs/notes.md) |
| Build locally and deliver to a directory or GitHub | [Local releases](docs/local-releases.md) |
| Keep a GitHub Actions publisher | [Actions adapter](docs/release-coordinator.md) |
| Use an agent-facing plugin | [Install and use the plugin](docs/plugin.md) |
| Integrate another local MCP client | [Standalone MCP](docs/mcp.md) |
| Consume machine-readable results | [CLI JSON contract](docs/cli-json.md) |

Installing/upgrading a plugin never changes a project's `.github/relkit.pyz`.
Each project tracks its own copy. Updating one does not update the others.

## Troubleshooting

| Symptom | Next check |
| --- | --- |
| `No module`, unsupported Python or missing `python` | Check `python --version`; use the intended interpreter, not an application's older environment |
| Scanner download/cache error | Check network access and `.cache/` ignore; `--no-download` only works with complete verified caches |
| Missing `relkit.toml` or secret policy | Run from the intended project root; check both files from the starter example |
| History refuses local changes | Use `audit`/`audit --staged` during editing; commit reviewed changes before `--history` |
| Existing projection or guard drift | Use the [update guide](docs/updates.md), not an overwrite or `--no-verify` |
| Plugin is not discovered or starts an old version | Follow [plugin troubleshooting](docs/plugin.md#troubleshooting), including native-client reload |

## Contributing and reporting problems

[CONTRIBUTING.md](CONTRIBUTING.md) describes isolated development setup, the base
check and the full distribution check. Maintainers use the
[distribution guide](docs/distribution.md) and the
[publication review](docs/publication-review.md) before making material public.
Report vulnerabilities privately using [SECURITY.md](SECURITY.md).

MIT licensed; see [LICENSE](LICENSE). The maintained engines retain their own
licenses and are obtained from their official releases, not copied into the CLI.
