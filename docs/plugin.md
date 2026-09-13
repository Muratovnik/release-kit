---
status: experimental
---

# Release Kit plugin

The plugin provides a workflow skill and MCP tools for the standalone release-kit
CLI. It has the same version as its bundled CLI. Installation never upgrades a
project's tracked `.github/relkit.pyz`; project synchronization is an explicit
`relkit_sync` operation.

## Install a published package

Use a local Codex client that supports `.codex-plugin/plugin.json`, `.mcp.json`
stdio servers and local marketplaces. Python 3.11+ must be available as `python`,
and `uv` must be on the client's PATH. A web-only client cannot launch this local
stdio process. Native-client availability varies; no numeric minimum client version
has been established for this project. Record the client version when validating
an installation rather than assuming that a new task refreshed its process.

Download `release-kit-plugin.zip` and its `.sha256` sidecar from the same reviewed
[release](https://github.com/Muratovnik/release-kit/releases). Example using an
already-published version, not the source template:

```text
gh release download v0.21.1 --repo Muratovnik/release-kit --pattern release-kit-plugin.zip --pattern release-kit-plugin.zip.sha256 --dir .cache/plugin-download
```

The browser download is equivalent; `gh` is not a plugin-runtime dependency.
Verify the file before extracting/executing it. In PowerShell:

```powershell
$expected = ((Get-Content .cache/plugin-download/release-kit-plugin.zip.sha256 -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash .cache/plugin-download/release-kit-plugin.zip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -notmatch '^[0-9a-f]{64}$' -or $actual -ne $expected) { throw 'Plugin checksum mismatch' }
```

In Bash:

```bash
(cd .cache/plugin-download && shasum -a 256 -c release-kit-plugin.zip.sha256)
```

Stop on any mismatch. A sidecar is not independent authentication; see
[distribution trust](distribution.md#integrity-and-trust). Choose a new, reviewed,
writable local marketplace directory. Do not overwrite an existing plugin or
marketplace catalog. Its layout should be:

```text
release-kit-marketplace/
  .agents/plugins/marketplace.json
  plugins/release-kit/
    .codex-plugin/plugin.json
    .mcp.json
    package.json
    scripts/launch.py
    lib/
    tools/relkit.pyz
    ...
```

Extract the archive under `release-kit-marketplace/plugins/`; the ZIP already
contains the `release-kit/` top-level directory. Python's built-in ZIP command works
on both platforms (use the full selected paths when not in the download directory):

```text
python -m zipfile -e .cache/plugin-download/release-kit-plugin.zip release-kit-marketplace/plugins
```

Create `.agents/plugins/marketplace.json` in that marketplace root:

```json
{
  "name": "local-release-kit",
  "plugins": [
    {
      "name": "release-kit",
      "source": {"source": "local", "path": "./plugins/release-kit"},
      "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
      "category": "Productivity"
    }
  ]
}
```

The path is relative to the **marketplace root**, not `.agents/plugins/`.
The example is also available at [examples/plugin-marketplace.json](../examples/plugin-marketplace.json)
in the source repository; the built package includes it as `examples/plugin-marketplace.json`.
Merge a single entry instead when using an existing catalog; preserve its other
entries and policy. Register the chosen root explicitly:

```text
codex plugin marketplace add /absolute/path/to/release-kit-marketplace
codex plugin marketplace list
```

In the supported desktop client's Plugins Directory, select `local-release-kit`
and install `release-kit`. Restart/reload the client to refresh discovery and the
running process. Registration/installation are user actions, not actions performed
by the launcher. These steps follow the
[official local-marketplace guide](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually).
They do not publish the package in the universal plugin directory or change
workspace administration settings.

## Verify the installation

From the installed package location, run:

```text
python /absolute/path/to/installed-release-kit/scripts/launch.py --check
```

Expect JSON with `valid: true` and equal `plugin`, `runtime`, `lock`, `cli` and
`inventory` component versions. This verifies packaged hashes/versions without
creating `.runtime/`; it does **not** prove MCP startup or native-client discovery.

Cold starts serialize runtime ownership and `uv sync --locked` using an OS file
lock at `.runtime.lock`. The lock file stays in place; do not delete it while
clients may be starting. Waiting is bounded to 150 seconds. Servers release this
lock before running, so multiple clients can remain connected independently.
A missing or mismatched receipt in an existing `.runtime/` still refuses startup;
install a fresh complete package instead of claiming that directory. Windows
runtime paths use the extended path format so nested caches and installed wheels
can exceed the legacy path limit without changing global system settings.

In a fresh client session, verify the release-kit skill and tools are discovered,
including `relkit_project`, `relkit_sync`, `relkit_audit` and `relkit_release`.
For a first useful read, use an existing project installed through the
[CLI quick start](../README.md#first-check-in-an-existing-project):

```json
{"request":{"action":"inspect","root":"<absolute-project-root>"}}
```

Send that to `relkit_project`. It should identify the intended canonical project,
projection version/hash and policy without executing project code or binding it.
Then send `{"request":{"action":"status","root":"<absolute-project-root>"}}`
to `relkit_sync`. Inspect the project/plugin versions and alignment; do not treat
an available update as permission to apply it. No release or hook is needed to
verify these reads.

The project's own ordinary `.git` directory, tracked `relkit.toml` and pinned
projection are required; project binding needs CLI 0.9.0+. Linked worktrees are
unsupported. Binding an unreviewed arbitrary repository is not an installation test.

## Runtime and package contents

The package contains a source snapshot, zipapp/checksum, skill, MCP configuration,
`uv.lock`, license and usage documentation. `package.json` records payload hashes;
it is a **hash inventory, not a digital signature**. Installation integrity depends
on the trusted distribution source; hashes do not make unreviewed code trustworthy.

The launcher uses the official MCP SDK 2.1.1 through the existing locked optional
environment. First startup downloads dependencies from lockfile URLs; it does not
download Python or build packages from source. Runtime/cache/temp files stay under
the installed plugin's `.runtime/`, requiring write permission for that exact path.
The environment is separate from the product's Python environment. Ownership/lock
drift refuses reuse; do not delete unknown files to force startup to pass.

Packaging/startup reject release-kit component version disagreement. Third-party
versions and historical changelog entries are independent. Install a complete new
version; editing a manifest as a cachebuster invalidates its payload inventory.
The launcher creates no daemon, Git hook or client registration and changes no
global Git configuration.

The MCP config uses `cwd: "."` and a relative launcher path, resolved against the
installed plugin root by the client's
[configuration loader](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/plugin_config.rs).
This selects the launcher, never the target project. It does not rely on shell
variable interpolation in MCP arguments.

## Use and authorization

For project updates, use `relkit_sync` with an explicit absolute `request.root`:
`status`, then `plan`. The verified bundled CLI inspects the existing projection
without executing its old code or requiring a binding. Preview may create owned
project-local locks/scratch; it is not a zero-write sandbox. Sync never obtains an
update candidate from GitHub, although apply may provision verified audit engines
unless `no_download` is true.

Review the plan and separate hook/rollback authorization before `apply` with
`plan_hash = result.data.plan_sha256`. An existing direct user request can be
relayed through the scoped authorization in the [MCP contract](mcp.md#existing-user-authorization);
otherwise native human confirmation is required. Rollback uses its own
`rollback_plan`/`rollback` pair. `aligned` means byte equality; newer projects or
different bytes under the same version are not overwritten. Updates report their
receipt/backup and expire existing bindings without disabling the bundled updater.

The process starts without a selected project. After `relkit_project inspect`,
`bind` accepts reviewed user authorization for checks or requests native confirmation.
Pass the returned opaque `binding` alongside `request` to workflow tools. There is
no mutable default root; interleaved projects cannot silently replace each other.
`unbind` takes only the binding. At most 32 in-memory bindings exist.

Restart, projection/policy drift or guard changes require fresh review/binding.
Project trust permits checks/preflights, not publication or hook mutation. Each
write retains its own plan, authorization and revalidation. Guard installation and
release run/resume can relay their own scoped user request, not update permission.
Notes export and alternate-source updates retain native confirmation. `resume_plan`
reviews the saved release and selected CI attempt before continuation.

A decline/cancel reports the client's action, not whether the human or host policy
caused it. Stop that attempt. Switching authorization routes or falling back to
CLI requires new user direction; never rewrite approval settings from a tool.
Native elicitation must reach the human and must not be auto-approved.

## Troubleshooting

| Symptom | Response |
| --- | --- |
| Plugin absent from the directory | Confirm marketplace root/path with `codex plugin marketplace list`; check the source is the extracted **built** package, then reload the supported client |
| `python` or `uv` not found | Check PATH in the environment that launches the client, not only a different terminal |
| Hash/version mismatch | Reinstall the complete reviewed version into a fresh path; do not edit manifest versions or inventory to suppress the error |
| Runtime ownership/lock drift | Preserve the old directory and diagnostics; install into a fresh explicitly approved writable location |
| Downloads fail | Check dependency-host access; do not relax the lock or enable source builds as a workaround |
| Old version persists | Reload/restart the client and check component versions; creating a new task alone does not restart the MCP process |
| Project inspection refuses | Check canonical root, normal `.git`, tracked policy/projection and supported version; do not bind a parent/sibling instead |
| Confirmation is declined | Stop and obtain new user direction; a matching plan hash is not consent |

## Build from source

For developers, [build the joint release set](distribution.md#identity-and-build)
with `python tools/build_release.py dist/VERSION`, then install the generated ZIP
using the procedure above. `tools/build_plugin.py` remains for plugin-only builds.
The template in `plugins/release-kit/` lacks the built source/CLI inventory and is
not the package to register.

The [full distribution check](../CONTRIBUTING.md#checks) tests the real extracted
launcher over stdio. That does not substitute for recording discovery and use in
the intended native client. Keep both results separate.

## Rollout, rollback and removal

Keep the prior package/checksum during rollout. Install side by side, verify native
discovery/read operations and remove only superseded task-owned registrations.
Preserve unrelated client entries and separately review updater backup retention.
External installation/backup paths require explicit agreement.

Uninstall through the native plugin manager, stop/reload the client and remove only
the owned marketplace entry/directory after reviewing its contents. This does not
remove project projections or hooks. Reinstalling an older plugin restores its
interface, not project state; project rollback uses `update --rollback` and its
receipt/authorization. Never mix an old registration with arbitrary new runtime
files. No live hosted release is part of installation or verification.
