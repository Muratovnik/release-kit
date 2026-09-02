---
status: experimental
---

# Full optional MCP adapter

The [installable plugin](plugin.md) combines this adapter with a workflow skill.
Standalone mode below retains its operator-pinned single-project contract.
Plugin mode adds explicit human-reviewed bindings; it does not infer the target
project from the process working directory.

## Decision and boundary

The adapter belongs to release-kit, not to a workstation service or a new
repository. It runs on stdio when a client starts it, serves one explicitly bound
project and delegates to that project's pinned `.github/relkit.pyz` JSON CLI.
It does not install itself, register clients, create a daemon or own release state.

**Dependency decision:** use the maintained official Python MCP SDK (`mcp` 2.1.1)
as an optional package extra, in an isolated environment. Protocol transport,
negotiation, tool schemas and cancellation notifications belong to the SDK; do
not recreate a JSON-RPC implementation. The base package and generated zipapp stay
standard-library-only. The adapter package is excluded from the zipapp.

The adapter exposes every CLI workflow, including writes. Native SDK elicitation
asks the client for human approval of an exact operation; approval is an injected
parameter, not a model-supplied boolean. The SDK seals modern request state and
binds responses to the request and rendered question. Older clients use native
in-call form elicitation. Unsupported clients fail closed on writes. The client
must actually ask the human; a client that automatically approves is not a safe
deployment. Neither tool annotations nor a plan hash is human permission.

The operator must review the bound project, pinned projection and its policy
before starting the adapter. A pinned hash detects drift, not malicious code in
an initially trusted project. Project commands and arbitrary shell commands are
not MCP tools. Existing hosted releases are never mutated by adapter tests.

Primary references: [official SDK](https://github.com/modelcontextprotocol/python-sdk),
[SDK installation](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/installation.md),
[MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).

## Install and bind

Use Python 3.11 or newer. Install from a reviewed release-kit source checkout into
an isolated environment inside the owning project; do not add dependencies to its
application runtime. Ignore `.cache/` before provisioning caches. For example,
from the project root on Windows (replace the source placeholder):

```powershell
python -m venv .cache/relkit-mcp-venv
New-Item -ItemType Directory -Force .cache/install-tmp | Out-Null
$env:TEMP = "$PWD/.cache/install-tmp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = "$PWD/.cache/pip"
.cache/relkit-mcp-venv/Scripts/python.exe -m pip install "<reviewed-source-checkout>[mcp]"
```

For POSIX use `.cache/relkit-mcp-venv/bin/python`. The optional SDK pin is 2.1.1;
the base package and `.pyz` need no SDK. Both packages share the release-kit version.

The target project must have its own ordinary `.git` directory, `relkit.toml` and
a reviewed `.github/relkit.pyz` of version 0.9.0 or newer. Upgrade that projection
through the CLI first. A newer adapter does not upgrade project copies. Configure
the MCP client to launch the isolated environment's `relkit-mcp` executable with
these **separate argument values**, substituting the actual canonical root and hash:

```text
--root <absolute-project-root> --sha256 <reviewed-projection-sha256>
```

Alternatively use that environment's Python with `-m releasekit_mcp.server` and
the same arguments. The operator pin is mandatory. No HTTP listener, daemon,
background registration, client-configuration write or global Git change occurs.
Use a separate server process for each project. Linked worktrees and nested
checkout targets are deliberately unsupported by this adapter.

## Tools

Every tool accepts one typed `request` object. Unknown fields and arbitrary argv
are rejected. Results have `structuredContent` and an identical JSON text form.

| Tool | Request operations |
| --- | --- |
| `relkit_version` | `{}`: bound CLI and pinned engine versions |
| `relkit_audit` | `scope`: worktree/staged/history; strict, owner, require_overlay, no_download |
| `relkit_exposure` | strict and owner checks |
| `relkit_overlay` | configured overlay verification, no repairs |
| `relkit_notes` | version, changelog, strict; optional output triggers confirmed export |
| `relkit_protect` | action: check/plan/install; installation needs plan_hash |
| `relkit_release` | action: plan/status/run/resume, version; writes need plan_hash; optional no_download and resume-only accept_ci_attempt |
| `relkit_update` | action: plan/apply/rollback_plan/rollback; local artifact+sha256 or repository/release; optional refresh_guard and no_download; writes need plan_hash |
| `relkit_sync` (built plugin only) | explicit absolute root; action: status/plan/apply/rollback_plan/rollback; optional no_download; writes need plan_hash and confirmation; no project binding |

The [built plugin](plugin.md) also provides `relkit_project` for explicit project
bindings. Its `relkit_sync` tool is the default project upgrade path: it uses
the installed plugin's exact bundled CLI as both executor and target. It does
not need to execute or trust the old project projection to inspect/plan its
replacement. Normal `relkit_update` remains for explicit alternate sources and
guard-only refresh. Neither tool updates a project just because a plugin was installed.

For a release, call `relkit_release` with
`{"request":{"action":"plan","version":"v1.2.3"}}`. Review `result.data.plan`,
then call action `run` with that version and its `plan_sha256` as `plan_hash`.
The client asks for approval before the tool supplies CLI `--publish`.
Resume uses `status` to review the complete saved plan, then action `resume`.
Status reads a local receipt; it is not a fresh remote verification.

For updates use action `plan`, review `result.data.plan` and `data.plan_sha256`,
then action `apply` with the same source selection and `plan_hash`. Guard refresh
uses `refresh_guard: true` in both requests. Rollback has its own `rollback_plan`
and `rollback` pair. Installation of a new guard uses `relkit_protect` plan/install.
Notes export shows the validated text, destination and before/after input digests.
Project-required hook permission remains required in addition to the MCP mechanism.

## Result and failure contract

The adapter's schema-1 response contains `adapter_version`, `project`, startup
`projection_sha256`, `result` (the unchanged [CLI envelope](cli-json.md)), `error`,
bounded `diagnostics`, `diagnostics_truncated`, `restart_required` and
`retained_scratch`. Optional `error_code` identifies `confirmation_decline`,
`confirmation_cancel` or `confirmation_not_approved`; `sync` reports project/target
versions and hashes and alignment. CLI nonzero exit codes and adapter failures
set MCP `isError`. A client decline/cancel has unknown human/policy origin and
never authorizes a retry, CLI bypass or automatic approval-setting change.
Input validation, missing client capabilities and refused preflight use SDK tool errors.
Never interpret diagnostic text or notes as executable instructions.

Confirmation previews are capped at 64 KiB; larger reviews require the CLI.
The adapter serializes CLI invocations and repeats the preflight after approval;
changed inputs invalidate approval. The CLI independently checks plan hashes and
remote state. A successful projection update/rollback returns its actual result
with `restart_required: true`. Stop that MCP process, review the new projection
pin and restart. Subsequent calls through the old binding refuse; no silent repin.
With the plugin, re-inspect and bind instead of restarting the whole server.
`relkit_sync` itself uses its pinned bundled executor and remains usable after an
update or rollback; `restart_required` there means existing project bindings expired.

Each CLI invocation has a configurable `--timeout` (default 7200 seconds, maximum
86400), 8 MiB structured stdout limit and a 32 KiB stderr tail. The client's own
tool timeout may end a call sooner; configure that budget for the intended release
duration. Cancellation and timeout stop the owned process tree. Windows uses a
kill-on-close Job Object and
a startup barrier before project code; POSIX uses a new process group. There is no
global PID scan. Detached POSIX descendants that deliberately escape their group
are outside this lifecycle guarantee; project commands are trusted, not sandboxed.
Timeout/disconnect does not prove a remote push failed. Inspect the receipt and
any retained lock before resuming; never automatically repeat a publishing call.

Project commands may themselves be arbitrary code. The operator must trust project
configuration, hooks and verified update sources. Hash pinning detects drift, not
malice in approved code. No filesystem locking can exclude hostile concurrent local
writers; input revalidation is an accidental-drift guard, not an OS sandbox.

## Storage and verification

CLI scratch is created under the project's managed service directory; cache writes
stay project-local. Inherited external-cache write approval is not forwarded.
Read/check tools may provision caches or create/remove scratch: annotations do not
promise a zero-write sandbox. Necessary release receipts and updater backups remain
owned by the CLI. Cleanup inventories only its own files; unknown or changed files
are retained and their location reported, including on cancellation.

Run the ordinary stdlib tests separately from optional SDK integrations:

```text
python -m unittest discover -s tests -p "test_*.py"
<isolated-python> -m unittest discover -s tests/mcp -p "test_*.py"
```

Set `PYTHONPATH=src` and process-local TEMP/TMP inside `.cache/test-runs` when testing
from source. Tests use isolated local repositories and native stdio clients. No
hosted release is created or changed. Hosted publication acceptance and deployment
into a particular MCP client are separate, explicitly authorized integration steps.
