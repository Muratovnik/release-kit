# Contributing

Use this repository as an independent Git root. Read [AGENTS.md](https://github.com/Muratovnik/release-kit/blob/main/AGENTS.md)
for ownership and change rules. Report ordinary bugs with a synthetic reproducer;
use [SECURITY.md](SECURITY.md) for sensitive reports.

## Isolated setup

Install Python 3.11+ and Git. From a reviewed source checkout, create a local
virtual environment; this repository already ignores `.cache/`:

```text
python -m venv .cache/dev-venv
```

In **PowerShell**:

```powershell
.cache/dev-venv/Scripts/Activate.ps1
$env:PIP_CACHE_DIR = "$PWD/.cache/pip"
New-Item -ItemType Directory -Force .cache/install-tmp | Out-Null
$env:TEMP = "$PWD/.cache/install-tmp"
$env:TMP = $env:TEMP
```

In **Bash**:

```bash
. .cache/dev-venv/bin/activate
mkdir -p .cache/install-tmp
export PIP_CACHE_DIR="$PWD/.cache/pip"
export TMPDIR="$PWD/.cache/install-tmp"
```

Then install the declared development tools:

```text
python -m pip install --disable-pip-version-check --requirement requirements-dev.txt
```

No editable application install is needed: the runners set the source path.
Keep cache/temp overrides local to this shell, not global settings.

## Checks

For CLI development, run `python tools/check.py`. It runs stdlib tests, Ruff lint
and format checks with a canonical temporary path, important on macOS.

For the full development check, install `uv` on PATH using its
[official instructions](https://docs.astral.sh/uv/getting-started/installation/), then run:

```text
python tools/check_distribution.py
```

This checks source, builds a test distribution, validates exact membership/hashes,
exercises real-scanner onboarding, installs the wheel and checks generated changelogs.
Missing dependencies and failed subprocesses are failures, not a base-only fallback.
The source phase alone (`--source-only`) needs neither uv nor the network.

Successful run fixtures below `.cache/release-kit-checks/` are removed; failed checks,
unknown run-root entries and leftover process scratch are retained for inspection.
Compact JSON reports remain in `reports/`, and their summary is printed to the job
log. See [distribution](docs/distribution.md) for storage, locking and explicit
snapshot-parent behavior.

The local release coordinator checks source with `--source-only`, then checks its
actual committed-source candidate with `--assets`, `--version` and `--work-dir`.
The manual release workflow follows the same order: source matrix, one candidate
build, one package matrix. It does not also qualify throwaway builds on each OS.
The separate manual check workflow retains the full development check. Neither
workflow runs automatically on pushes/PRs/tags or publishes a release.

## Change and release discipline

Keep patches focused. Add regression tests at the affected boundary; a mocked
process returning zero does not establish startup or real dependency integration.
Report actual commands, outcomes and missing verification, including platform gaps.

Add user-facing changes under `Unreleased`, with required action and compatibility
first. Link engineering detail instead of rewriting history. Before distributing
new bytes, advance all component versions and add the dated entry as described in
[distribution](docs/distribution.md). Never replace published assets under an old
version, including documentation-only rebuilds.

Publishing, changing visibility and rewriting history are separate maintainer
actions. Opening or merging a PR does not authorize them. Tests use disposable
local fixtures, not real adopter hooks or live hosted publications.
