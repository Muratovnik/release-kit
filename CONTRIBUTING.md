# Contributing

Use this repository as an independent Git root. Read [AGENTS.md](https://github.com/Muratovnik/release-kit/blob/main/AGENTS.md) for
ownership and change rules. Report ordinary bugs with a synthetic reproducer;
use [SECURITY.md](SECURITY.md) for sensitive reports.

## Isolated setup

Install Python 3.11+ and Git. From a reviewed source checkout, ignore `.cache/`
(the repository already does), then create a local virtual environment:

```text
python -m venv .cache/dev-venv
```

Activate it in **PowerShell**:

```powershell
.cache/dev-venv/Scripts/Activate.ps1
$env:PIP_CACHE_DIR = "$PWD/.cache/pip"
New-Item -ItemType Directory -Force .cache/install-tmp | Out-Null
$env:TEMP = "$PWD/.cache/install-tmp"
$env:TMP = $env:TEMP
```

Or in **Bash**:

```bash
. .cache/dev-venv/bin/activate
mkdir -p .cache/install-tmp
export PIP_CACHE_DIR="$PWD/.cache/pip"
export TMPDIR="$PWD/.cache/install-tmp"
```

Then install the declared development tools in that environment:

```text
python -m pip install --disable-pip-version-check --requirement requirements-dev.txt
```

No editable application install is needed: the check runners set the source path
explicitly. Keep cache/temp overrides local to this shell, not global settings.

## Checks

For ordinary CLI development, run:

```text
python tools/check.py
```

This executes stdlib tests, Ruff lint and Ruff format checks. It also canonicalizes
the temporary directory (important on macOS). Bare `unittest` plus `ruff check`
is not equivalent: it omits formatting and runner setup. The base check deliberately
does not install/import the optional MCP SDK.

Before a **joint CLI/plugin distribution**, install `uv` on PATH using its
[official installation instructions](https://docs.astral.sh/uv/getting-started/installation/),
then run:

```text
python tools/check_distribution.py
```

The full development check runs the base gate, explicitly discovers `tests/mcp` in
an isolated SDK environment using the existing plugin `uv.lock`, builds a test
release set, validates exact membership and hashes, exercises CLI onboarding, and
starts the extracted plugin through its own `.mcp.json` with a native SDK client.
Missing SDK/uv, empty MCP collection, failed subprocesses or failed package checks
are failures, not skips or a fallback to the base gate.

The first locked SDK/plugin startup and full audit may need downloads. All check
outputs and caches stay in managed project-local locations; no global packages,
client registrations, Git hooks or hosted publications are created. The built
plugin's own `.runtime/` is intentionally retained with the check's diagnostics,
not silently swept as unknown files. The report identifies the host and artifact
digests. It is evidence for that host, not a claim that other platforms ran.

Both hosted workflows remain **manual**. Ordinary pushes/PRs do not consume hosted
minutes. Successful local checks do not imply a successful hosted matrix.
The local release coordinator separates source checks from candidate qualification:
`check_distribution.py --source-only` runs base/MCP tests, then the configured smoke
checks the actual committed-source candidate with `--assets`, `--version` and an
explicit `--work-dir`. It does not substitute a passing development build for the
files that will be published. Git-free snapshots and separate asset directories
are supported by that package mode; see [distribution](docs/distribution.md).

For targeted MCP work, use the locked environment created by the full check to
run `tools/check_mcp.py`, or invoke it through `uv run --locked` with the same
plugin project and local environment/cache settings. Do not add `tests/mcp/__init__.py`
just to make the base suite import optional dependencies.

## Change and release discipline

Keep patches focused and add a regression test that exercises the affected user
boundary. Test discovery, startup and real dependency integration are different
claims; a mocked process returning zero proves none of the latter two.
Do not make an optional platform test silently satisfy a required release check.

Add a user-facing entry under `Unreleased`: state what changed, whether action is
needed and any compatibility limits first. Link detailed engineering rationale
rather than replacing historical release notes. Before distributing new bytes,
advance all release-kit component versions together and add the dated entry as
specified in [distribution](docs/distribution.md). Never replace existing published
assets under the old version, including documentation-only package rebuilds.

A pull request should state the source revision, changes, executed commands and
results, and remaining checks. Do not describe proposed checks as completed ones.
Publishing tags/assets, changing visibility and rewriting history are separate
maintainer actions; opening or merging a PR does not authorize them.
