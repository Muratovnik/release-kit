---
status: experimental
---

# Release Kit plugin

One installable package combines the release-kit workflow skill and all MCP
workflows. It belongs to release-kit and uses the same version as its source
package. The plugin does not replace the standalone CLI or the project's pinned
`.github/relkit.pyz`; a plugin upgrade never upgrades a project automatically.
The plugin and its bundled CLI share one release version. `relkit_sync` is the
explicit path for synchronizing an existing project to that exact distribution.

## Build and install

From a reviewed source checkout, run `python tools/build_release.py
dist/<version>`. It builds one plugin and exports its exact bundled `relkit.pyz`,
both checksum sidecars and `release.json`. Publish these as one versioned release
set; the builder refuses to overwrite an existing nonempty output directory.
`tools/build_plugin.py` remains available for plugin-only development builds.
Extract the archive's `release-kit/` directory into
the chosen plugin source directory, then add it to a native Codex marketplace
and install through the native plugin manager. See the
[official packaging guide](https://developers.openai.com/plugins/build/plugins).
The source template in `plugins/release-kit/` is not the built installable package.

The package includes a source snapshot, the standalone zipapp and its checksum,
one skill, an MCP configuration and `uv.lock`. Its inventory `package.json`
records hashes of all payload files. Builds are deterministic; no editable
checkout, user path, project list, virtual environment or secret is packaged.

Prerequisites are Python 3.11+ and `uv` on the client's PATH. The launcher uses
the official MCP SDK 2.1.1 through a locked optional environment. Dependencies
are downloaded from the lock's package URLs on first startup; no Python is
downloaded and source builds are disabled. All runtime/cache/temp files stay
under the installed plugin's `.runtime/`; they do not enter the product's Python
environment. Installation therefore requires a writable plugin directory and
permission for that exact location. Runtime ownership or lock drift refuses
reuse; do not delete unknown files to make startup pass.

`python <installed-plugin>/scripts/launch.py --check` verifies packaged hashes
and reports the manifest, runtime, lock, inventory and CLI versions together,
without creating a runtime. Packaging and startup refuse version disagreement;
third-party dependency versions and historical changelog entries are independent.
Install the complete versioned package, without changing its manifest after
building: a plugin-only cachebuster would invalidate both version equality and
the signed payload inventory. A new distribution version already provides a new
installation identity. Normal startup never creates a daemon, installs a
Git hook, registers another client or changes global Git configuration.

The MCP launcher uses `cwd: "."` and a relative script path. Codex resolves that
working directory against the installed plugin root in its
[plugin configuration loader](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/plugin_config.rs).
It does not rely on shell-variable interpolation in MCP arguments. This working
directory selects the launcher only, never the target project.

## Use

Verify the discovered skill, tool schema and adapter version after installation.
A running client may retain an older plugin process; reload or restart that
client if needed. A new task alone does not prove that the runtime was refreshed.
Invoke the release-kit skill or ask for a release-kit check/update/plan.

For an update, use `relkit_sync` with an explicit absolute `request.root` and
`action = "status"`, then `"plan"`. It reports both versions and hashes, and
uses the verified bundled CLI for the existing transactional updater. Preview
does not execute the project's old code and requires no binding, including for
pre-MCP project versions. This is not zero-write sandboxing: owned temporary
files/locks can be created under the project. It never publishes or fetches a
candidate from GitHub. Audit engines may still need their verified downloads
during apply, unless `no_download` is true.

After reviewing the plan and any separate hook/rollback authorization, call
`"apply"` with `plan_hash = result.data.plan_sha256`. When the user already
requested this update, pass the scoped `authorization` described in the
[MCP contract](mcp.md#existing-user-authorization): no second dialog is needed.
Otherwise native confirmation remains required. The same tool supports `rollback_plan`
and `rollback` using the saved project receipt. `aligned` is byte-for-byte equality;
newer projects and different bytes under the same version are not overwritten.
Successful updates report `sync.state`, backup and receipt; existing bindings
must be renewed, but the plugin updater remains available without restart.

The plugin process deliberately starts without a selected project. Call
`relkit_project` with `request = {"action": "inspect", "root": "<absolute-root>"}`
to read distribution and policy metadata without running project code. The
project must be an ordinary checkout with its own `.git` directory and a pinned
projection at version 0.9.0 or newer. Linked worktrees remain unsupported.

Action `bind` with the same root accepts existing user authorization for the
reviewed checks, or asks for native confirmation, then returns an opaque `binding`. Pass that value
alongside `request` to each of the eight workflow tools. No mutable default root
exists, so interleaved calls for two projects cannot silently switch targets.
Action `unbind` takes only a binding and forgets it. At most 32 bindings are held.

Bindings are in memory only. Restart, projection drift, policy drift or guard
changes require a fresh inspection and authorization review. Native elicitation,
when used, must reach the human; automatically approving it is not supported.
Project trust permits CLI checks/preflights, not publication or hook mutation.
Every write still uses its existing plan, authorization and revalidation boundary.
Guard installation and release run/resume can use their own scoped user-request
authorization and fresh operation preview; update authority cannot be reused for
publication. Native confirmation remains the fallback, and is still required for
notes export and alternate-source updates. Use `resume_plan` to review the saved
release and any selected CI attempt before authorized continuation.
A `confirmation_decline` or
`confirmation_cancel` result reports the client action, not its human/policy
origin. Stop that attempt; changing authorization path or using CLI requires new
user direction. Never rewrite client approval settings from a tool.
See the bundled skill for the
update and release workflows, and [the MCP contract](mcp.md) for CLI failure,
cancellation and recovery semantics.

## Rollout and rollback

Keep a prior plugin package and checksum during rollout. Install side by side,
verify native discovery and read-only calls, then remove only superseded,
task-owned manual registration entries. Preserve all unrelated client entries.
Do not remove project updater backups or unused environments without reviewing
their ownership and retention separately. Project service files stay within the
project; an external installation/backup path needs explicit agreement.

Reinstalling the previous plugin package restores its interface, not a project's
projection or hooks. Those use the project's own `update --rollback` receipt and
authorization. Do not mix old plugin registrations with arbitrary new runtimes.
No live hosted release is part of plugin installation or verification.
