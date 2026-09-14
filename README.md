# release-kit

[![Release](https://img.shields.io/github/v/release/Muratovnik/release-kit?label=release&color=blue)](https://github.com/Muratovnik/release-kit/releases)
[![License](https://img.shields.io/github/license/Muratovnik/release-kit?color=blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Downloads](https://img.shields.io/github/downloads/Muratovnik/release-kit/total?label=downloads)](https://github.com/Muratovnik/release-kit/releases)

Check a Git repository before you publish it. One command runs secret scanning,
Markdown link checks and the file and history rules a project declares for itself.
It can also prepare release files, deliver them to a directory or GitHub, and verify
what was published.

## What it looks like

```text
$ relkit audit
relkit audit: passed
```

```text
$ relkit audit
[README.md]:
[ERROR] .../docs/deploy.md (at 3:9) | File not found. Check if file exists and path is correct

🔍 1 Total (in 0s) 🔗 1 Unique ✅ 0 OK 🚫 1 Error

relkit audit: failed
  Lychee failed
```

Exit `0` means the configured checks passed for that scope. Exit `1` means findings:
read the paths and fix or review them. Exit `2` means configuration or execution
failed, which is not a clean security result. For automation use `audit --json` and
the [result contract](docs/cli-json.md).

## Install

One tool, one configuration per project — the same shape as any other linter. Pick
the form that matches who runs it.

| Form | Install | Version comes from |
| --- | --- | --- |
| On your machine | `uv tool install <wheel URL>` | your machine |
| Pinned in a repository | the zipapp, committed to the project | the committed bytes |
| A Python project's dev group | `uv add --dev "release-kit @ <wheel URL>"` | that project's lockfile |

**For CI and Git hooks the project form is authoritative.** A machine-level install is
a convenience; it is not what makes a clone reproducible.

### On your machine

```text
uv tool install https://github.com/Muratovnik/release-kit/releases/download/v0.25.0/release_kit-0.25.0-py3-none-any.whl
```

`pipx install <same URL>` works the same way. This installs the published artifact, but
it does **not** check the file against its published checksum. To verify first:

```text
gh release download v0.25.0 --repo Muratovnik/release-kit --pattern 'release_kit-*' --dir .cache/relkit-download
python -c "import glob,hashlib,pathlib,sys; w=glob.glob('.cache/relkit-download/*.whl')[0]; e=pathlib.Path(w+'.sha256').read_text().split()[0]; a=hashlib.sha256(pathlib.Path(w).read_bytes()).hexdigest(); sys.exit('checksum mismatch') if a!=e else print(w)"
uv tool install .cache/relkit-download/release_kit-0.25.0-py3-none-any.whl
```

A checksum published beside a file detects corruption; it is not independent publisher
authentication. Where available, verify the immutable release with `gh release verify`
and `gh release verify-asset`. See [trust boundaries](docs/distribution.md#integrity-and-trust).

### Pinned in a repository

The zipapp needs only Python on PATH, so it suits any repository, including ones with
no Python packaging at all. Download `relkit.pyz` and `relkit.pyz.sha256` from the same
reviewed release, then verify and place them with one command that works in every shell:

```text
gh release download v0.25.0 --repo Muratovnik/release-kit --pattern 'relkit.pyz*' --dir .cache/relkit-download
python -c "import hashlib,pathlib,shutil,sys; s=pathlib.Path('.cache/relkit-download'); t=pathlib.Path('.github/relkit.pyz'); e=(s/'relkit.pyz.sha256').read_text().split()[0]; a=hashlib.sha256((s/'relkit.pyz').read_bytes()).hexdigest(); sys.exit('checksum mismatch') if a!=e else None; sys.exit('already installed: use the update guide') if t.exists() else None; t.parent.mkdir(parents=True,exist_ok=True); shutil.copy(s/'relkit.pyz',t)"
```

Run it as `python .github/relkit.pyz audit`. Commit the file; each project tracks its
own copy, and updating one does not update the others. Later changes go through the
[update guide](docs/updates.md), never an overwrite.

## First check in an existing project

Work from the root of the Git repository you want to check. No hook, private sibling,
MCP client or hosting account is needed for this path.

Create `relkit.toml`, merging deliberately if the file already exists:

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

Add `.cache/` to the project's `.gitignore` **before running the audit**, keeping any
existing rules, and create the directory so the first audit validates the directory-only
ignore before any scanner writes to it:

```text
python -c "from pathlib import Path; Path('.cache').mkdir(exist_ok=True)"
```

These exact starter files also live in [examples/audit](examples/audit/README.md). No
scanner is disabled in this example. Then:

```text
relkit audit
```

The first audit may download the pinned scanner archives. It checks tracked files and
untracked, unignored publication candidates, and does not stage, commit, install a
hook, tag, push or publish. Downloads and verified scanners belong in `.cache`, never
in Git.

After committing the configuration, check the complete local history:

```text
relkit audit --history
```

History needs a non-shallow clone and a clean tracked tree, and cannot inspect refs
that were never fetched. Existing historical findings may require a separate
owner-approved response; do not rewrite history just to obtain a pass.

## Requirements and limits

| Use | Requirements |
| --- | --- |
| CLI | Python 3.11+ and Git on PATH |
| Machine install | `uv` or `pipx`; the wheel itself has no runtime dependencies |
| Full audit | Pinned Betterleaks and Lychee executables; the first audit downloads and verifies them |
| Full audit platforms | Linux x64/arm64 (GNU builds), macOS x64/arm64, Windows x64 |
| GitHub delivery or updates | GitHub CLI; delivery needs `gh` 2.98.0+, authentication and the documented repository permissions |
| Plugin | Python 3.11+, `uv`, a writable installation directory and a local client supporting this plugin's stdio configuration |

The Python CLI and the external scanners have different platform requirements. There is
no pinned native Windows arm64 Lychee asset. Unknown or unpinned scanner platforms
refuse rather than select an unverified binary. See
[distribution and platform details](docs/distribution.md).

The audit is not a general security assessment or proof that a project is safe to
publish. Private names must be declared by their owner. Built-in text rules use specific
patterns, not general language understanding. The audit does not inspect GitHub issues,
Actions logs or un-fetched history. LFS pointers, submodules and unsupported compressed
archives fail the relevant inspection. Linked worktrees are not supported by the updater,
release coordinator or MCP adapter. Read the [scope and exclusions](docs/audit.md#scopes)
before adopting the gate.

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

## Troubleshooting

| Symptom | Next check |
| --- | --- |
| `No module`, unsupported Python or missing `python` | Check `python --version`; use the intended interpreter, not an application's older environment |
| Scanner download or cache error | Check network access and the `.cache/` ignore; `--no-download` only works with complete verified caches |
| Missing `relkit.toml` or secret policy | Run from the intended project root; check both files from the starter example |
| History refuses local changes | Use `audit` or `audit --staged` while editing; commit reviewed changes before `--history` |
| A machine install and a project pin disagree | Prefer the project form for CI and hooks; compare `relkit --version` with the pinned copy |
| Existing projection or guard drift | Use the [update guide](docs/updates.md), not an overwrite or `--no-verify` |
| Plugin is not discovered or starts an old version | Follow [plugin troubleshooting](docs/plugin.md#troubleshooting), including native-client reload |

## Contributing and reporting problems

[CONTRIBUTING.md](CONTRIBUTING.md) describes isolated development setup, the base check
and the full distribution check. Maintainers use the
[distribution guide](docs/distribution.md) and the
[publication review](docs/publication-review.md) before making material public. Report
vulnerabilities privately using [SECURITY.md](SECURITY.md).

MIT licensed; see [LICENSE](LICENSE). The maintained engines retain their own licenses
and are obtained from their official releases, not copied into the CLI.
