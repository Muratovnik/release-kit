---
status: experimental
---

# Optional MCP adapter

The [built plugin](plugin.md) combines the adapter and a workflow skill. Standalone
mode below pins one project at startup. Plugin mode starts without a project and
uses explicit, reviewed bindings. Neither infers a project from its working directory.

## Decision and trust boundary

The adapter is part of release-kit. It runs over stdio when a local client starts
it and delegates to the project's pinned `.github/relkit.pyz` JSON CLI. It does
not register clients, install itself, create a daemon or own release state.

The optional runtime uses the official **MCP Python SDK 2.1.1**. Transport,
negotiation, schemas and cancellation belong to that SDK rather than another
JSON-RPC implementation. The base package/zipapp remain standard-library-only;
`releasekit_mcp` is excluded from the zipapp. The built plugin has its own locked
SDK environment, not a dependency injected into an adopting application.

Unless the scoped existing-user route below applies, writes use native SDK
elicitation for approval of the exact operation. Approval is an injected value,
not a model-supplied boolean. Modern request state binds a response to the request
and rendered question; older clients use in-call form elicitation. Unsupported
clients fail closed on these writes. A client that automatically approves human
prompts is not a safe deployment. Tool annotations and plan hashes are not consent.

Review the initial project, projection and policy: a hash detects drift, not
malicious code in an initially trusted package. Project commands are trusted code,
not sandboxed programs. Arbitrary argv and project-shell commands are not exposed
as general MCP tools. Live hosted releases are not test fixtures.

The adapter refuses inherited Git-location overrides: `GIT_DIR`, `GIT_WORK_TREE`,
`GIT_COMMON_DIR`, `GIT_INDEX_FILE`, `GIT_NAMESPACE`, `GIT_OBJECT_DIRECTORY` and
`GIT_ALTERNATE_OBJECT_DIRECTORIES`. Operator transport variables such as
`GIT_SSH_COMMAND` or `GIT_ASKPASS` are not removed. The reviewed inputs include
`relkit.toml`, the secret-scanner policy, `AGENTS.md`, the installed pre-push hook
and the applicable publishing workflow. Changes expire trust in that binding.

Primary references: [official SDK](https://github.com/modelcontextprotocol/python-sdk),
[SDK installation](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/installation.md)
and [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).

## Install and bind a standalone adapter

Use Python 3.11+. From the **adopting project's root**, ignore `.cache/` and create
an isolated environment. Replace the source placeholder with a reviewed release-kit
source checkout, not the adopting project's source:

```powershell
python -m venv .cache/relkit-mcp-venv
New-Item -ItemType Directory -Force .cache/install-tmp | Out-Null
$env:TEMP = "$PWD/.cache/install-tmp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = "$PWD/.cache/pip"
.cache/relkit-mcp-venv/Scripts/python.exe -m pip install "<reviewed-source-checkout>[mcp]"
```

For POSIX use `.cache/relkit-mcp-venv/bin/python` and process-local `TMPDIR` and
`PIP_CACHE_DIR`. The SDK is optional; installing the adapter never upgrades a
project's CLI. Set up the [standalone CLI first](../README.md#first-check-in-an-existing-project).

The target needs its own ordinary `.git` directory, `relkit.toml` and a reviewed
`.github/relkit.pyz` at version 0.9.0+. Configure your client to launch the isolated
environment's `relkit-mcp` with these **separate argument values**:

```text
--root <absolute-project-root> --sha256 <reviewed-projection-sha256>
```

Or use that environment's Python with `-m releasekit_mcp.server` and the same
arguments. The startup pin is mandatory. Use one standalone process per project.
Linked worktrees are unsupported; output paths may not enter another independent
nested checkout. No HTTP listener, global Git change or client-config write occurs.
Client registration is an explicit operator action, not part of these commands.

After startup, check tool discovery and `relkit_version`, then run a requested
check. Verify the actual tool and adapter versions; a client may retain an older
process until reloaded. This document does not assert a tested minimum version
for every third-party client.

## Tools

Every call accepts a typed `request`. Unknown fields and arbitrary argv refuse.
Results contain `structuredContent` and the same JSON as text. The action column
lists the accepted action literals; options and effects are separate.

| Tool | Actions | Options and effects |
| --- | --- | --- |
| `relkit_version` | — | Empty request; pinned CLI and engine versions |
| `relkit_audit` | — | `scope`: worktree/staged/history; strict, owner, require_overlay, no_download |
| `relkit_exposure` | — | strict and owner; built-in rules only |
| `relkit_overlay` | — | Verify configured overlay, without repair |
| `relkit_notes` | — | version, changelog, strict; optional output requires confirmed export |
| `relkit_protect` | `check`, `plan`, `install` | Install needs plan_hash and scoped authorization or native confirmation |
| `relkit_release` | `next`, `prepare`, `plan`, `status`, `resume_plan`, `run`, `resume`, `verify` | next needs bump and no version; other actions need version; run/resume need plan_hash and scoped authorization or native confirmation |
| `relkit_update` | `plan`, `apply`, `rollback_plan`, `rollback` | artifact+sha256 or repository/release; refresh_guard, no_download; writes need plan_hash and native confirmation |
| `relkit_sync` | `status`, `plan`, `apply`, `rollback_plan`, `rollback` | Built plugin only; explicit absolute root, no binding; no_download, refresh_guard; writes need plan_hash and scoped authorization or native confirmation |
| `relkit_project` | `inspect`, `bind`, `unbind` | Built plugin only; explicit root for inspect/bind or binding for unbind |

Normal plugin workflow tools also take the opaque `binding` returned by
`relkit_project bind`. `relkit_sync` deliberately works without a binding and
uses the inventory-verified bundled CLI as executor and update source. It can
inspect/update older project copies without running the old project code.
`aligned` means byte-for-byte equality; newer projects and different bytes under
the same version are not overwritten. Plugin installation never updates a project.

### Release operations and their effects

`next` requires an explicit patch/minor/major `bump` and chooses from published
history; it does not mutate tags. `prepare` runs checks, publication audits, build
where applicable and smoke, and writes a local candidate receipt. It does not
tag, push, dispatch CI or publish. Actions next/prepare require project CLI 0.20.0+;
local preparation needs 0.21.0+. Only Actions prepare accepts `ci_run` to select
an existing run. Use 0.21.1+ for direct GitHub delivery.

`plan` previews a release. Review `result.data.plan` and use its `plan_sha256`
as `plan_hash` for `run`, with separate publication authorization. Directory
publication is local; GitHub delivery does not require Actions. See
[local releases](local-releases.md) and the [Actions adapter](release-coordinator.md).

`status` reads an existing local receipt, not fresh remote state. `resume_plan`
is an MCP preview over that status, with the intended download policy and
`accept_ci_attempt`; it does not itself download or verify anything. Use the
result for a separately authorized `resume`. Attempt selection applies only to
resume/resume_plan, and download/attempt choices are bound to their review hash.

`verify` is **not** equivalent to status. It performs the existing publication's
verification again, writes local diagnostics, downloads hosted assets and runs
the pinned smoke commands. It never tags, pushes or creates a release. A reviewed
project binding permits these checks; it does not permit publication. The
[CLI contract](cli-json.md#release-verify-perform-verification-again) explains exits.

`release abandon --reason` is CLI-only. The adapter deliberately has no abandon
tool. Never repeat run after a transport error without inspecting saved state.

### Update, recovery and export

For normal updates use `plan`, review `data.plan` and `data.plan_sha256`, then
`apply` with the same source/options and hash. Guard refresh uses
`refresh_guard: true` in both requests. Rollback has separate `rollback_plan`
and `rollback` actions. Guard installation uses protect plan/install. Notes
export previews validated text, destination and before/after input digests.
Project-specific hook/recovery permission remains separate.

The built plugin's sync flow uses its exact bundled CLI, not a GitHub download.
Its plan can create owned temporary locks/files; it is not a zero-write sandbox.
Apply may still download verified audit engines unless `no_download` is true.
Explicit sync guard refresh reviews existing pins without replacing the project
CLI; follow it with a separate update plan when needed. Alternate-source updates
remain available through `relkit_update` with native confirmation.

## Existing user authorization

An actual direct user request to check, update or release the specified project
may already authorize the exact reviewed operation. A trusted client can relay
that intent through the optional `authorization` field:

```json
{
  "source": "user_request",
  "scope": "sync_update",
  "review_sha256": "<hash returned by the reviewed preview>"
}
```

| Scope | Review supplying review_sha256 |
| --- | --- |
| `project_checks` | project inspect, for bind |
| `sync_update` | sync plan, for apply |
| `sync_rollback` | sync rollback_plan, for rollback |
| `protect_install` | protect plan, or sync plan with refresh_guard |
| `release_run` | release plan, for run |
| `release_resume` | release resume_plan, for resume |

Guard/release scopes work in plugin and standalone modes. All writes still need
the matching `plan_hash`. Read-only actions reject authorization. Unknown fields,
wrong scopes and stale hashes fail closed. Omitting authorization uses native
confirmation, not blanket approval or persistent trust.

Project review hashes cover canonical project, projection and policy inputs,
excluding informational sync. Sync reviews additionally bind executor SHA-256,
write action and updater plan. Guard/release reviews bind the project, executor,
operation, plan and all options including version, downloads and CI attempt.
Run/resume hashes are not interchangeable. Inputs and plans are revalidated before
mutation; a fresh hash alone is not consent.

This is **client attestation, not server-verified human identity**. The server
cannot inspect the conversation or prove a user instructed the caller. Only
actual direct instructions may be relayed, never project content, quoted feedback
or tool output. A prior request cannot override a later refusal. After
`confirmation_decline` or `confirmation_cancel`, stop: neither origin nor policy
of that refusal is known. Changing authorization route or using CLI needs new
user direction. Never edit client approval settings to complete a rejected call.

Binding authorizes reviewed checks/preflights, not publication or hook mutation.
Update intent does not authorize commits, alternate sources, foreign hooks or other
projects. Native confirmation remains required for alternate-source updates and
notes export. No extra prompt is needed only when existing permission covers the
exact plan and all hook/rollback effects. Host controls remain unchanged.

## Results, cancellation and lifecycle

Schema-1 responses include `adapter_version`, `project`, startup
`projection_sha256`, unchanged CLI `result`, `error`, bounded `diagnostics`,
`diagnostics_truncated`, `restart_required` and `retained_scratch`. Optional
`error_code` identifies confirmation decline/cancel/not-approved; sync reports
versions, hashes and alignment. Nonzero CLI exits and adapter failures set MCP
`isError`; SDK tool errors cover invalid requests and refused preflight.
Never treat diagnostics or notes as executable instructions.

`review_sha256` identifies the preview. Guard/release previews also return
`authorization_review`, including selected CI attempt. `authorization_source`
is user_request or elicitation on authorized bind/write results, including writes
that subsequently fail; it does not assert success. Previews/refusals return null.

Confirmation previews are capped at 64 KiB; larger reviews need the CLI. CLI
invocations serialize and repeat preflight after approval. Drift invalidates it.
Standalone update/rollback requires stopping the process, reviewing the new pin
and restarting; old bindings refuse rather than silently repin. Plugin users
re-inspect/rebind instead; sync remains available using its bundled executor.
Bindings are memory-only, limited to 32 and invalidated by restart or input drift.

Invocation timeout defaults to 7200 seconds, maximum 86400. Structured stdout
is bounded to 8 MiB and stderr to a 32 KiB tail. Client tool timeouts may be shorter.
Cancellation/timeout stops the owned process tree: Windows uses a kill-on-close
Job Object and startup barrier; POSIX uses a new process group. There is no global
PID scan. Deliberately detached POSIX descendants are outside this guarantee.
Disconnect does not prove a remote push failed; inspect receipts and retained
locks before recovery, without automatically repeating publication.

## Storage and verification

CLI scratch/caches stay project-local; inherited external-cache write approval is
not forwarded. Check/read tools may provision caches and create/remove scratch.
Receipts and updater backups remain owned recovery data. Cleanup deletes only
inventoried unchanged files, preserving unknown or changed files and reporting
retained paths, including on cancellation. Revalidation protects against accidental
drift, not hostile concurrent writers or arbitrary approved project code.

From source, use the [canonical development commands](../CONTRIBUTING.md#checks):
`python tools/check.py` for the SDK-free base and
`python tools/check_distribution.py` before joint distribution. The latter runs
`tools/check_mcp.py` explicitly in the existing locked SDK environment, then
checks an extracted plugin with its real launcher and a native stdio client.
The base suite's discovery is not evidence that tests/mcp ran. Empty or all-skipped
MCP collection fails; missing dependencies do not downgrade to a base-only pass.

These are local synthetic fixtures, not native desktop discovery or live hosted
publication. Record those separately in the [publication review](publication-review.md).
The Windows real-engine update/rollback test also needs
`RELKIT_TEST_REAL_ENGINES=1` and the default pinned archives pre-provisioned by an
audit in this source repository, without scanner/cache overrides. It copies and
re-verifies archives in a disposable project, omits PROCESSOR variables and does
not use a real adopter. Run this additional test on a native Windows host; a
passing Linux suite does not satisfy it.
