---
status: experimental
---

# Release Kit plugin

One installable package combines the release-kit workflow skill and all MCP
workflows. It belongs to release-kit and uses the same version as its source
package. The plugin does not replace the standalone CLI or the project's pinned
`.github/relkit.pyz`; a plugin upgrade never upgrades a project automatically.

## Build and install

From a reviewed source checkout, run `python tools/build_plugin.py
dist/release-kit-plugin.zip`. Extract the archive's `release-kit/` directory into
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
without creating a runtime. Normal startup never creates a daemon, installs a
Git hook, registers another client or changes global Git configuration.

The MCP launcher uses `cwd: "."` and a relative script path. Codex resolves that
working directory against the installed plugin root in its
[plugin configuration loader](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/plugin_config.rs).
It does not rely on shell-variable interpolation in MCP arguments. This working
directory selects the launcher only, never the target project.

## Use

Start a fresh client task after installation so it discovers the skill and MCP
tools. Invoke the release-kit skill or ask for a release-kit check/update/plan.

The plugin process deliberately starts without a selected project. Call
`relkit_project` with `request = {"action": "inspect", "root": "<absolute-root>"}`
to read distribution and policy metadata without running project code. The
project must be an ordinary checkout with its own `.git` directory and a pinned
projection at version 0.9.0 or newer. Linked worktrees remain unsupported.

Action `bind` with the same root asks for human trust of the displayed project,
code hash and policy inputs, then returns an opaque `binding`. Pass that value
alongside `request` to each of the eight workflow tools. No mutable default root
exists, so interleaved calls for two projects cannot silently switch targets.
Action `unbind` takes only a binding and forgets it. At most 32 bindings are held.

Bindings are in memory only. Restart, projection drift, policy drift or guard
changes require a fresh inspection and confirmation. Native elicitation must
reach the human; automatically approving it is not a supported deployment.
Project trust permits CLI checks/preflights, not publication or hook mutation.
Every write still uses the existing plan, confirmation and revalidation boundary.
Unsupported or rejecting clients fail closed. See the bundled skill for the
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
