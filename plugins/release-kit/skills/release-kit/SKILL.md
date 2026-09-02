---
name: release-kit
description: Use release-kit to check publication readiness, review project-tool updates, prepare releases and safely resume interrupted releases in repositories that adopt relkit.toml and .github/relkit.pyz.
---

# Release Kit

Establish the intended repository from the user's task, not the MCP process's
working directory. Read that repository's instructions and inspect Git status.
Do not adopt a new release policy when the user only asked to run existing checks.

## Select and verify the project

With plugin MCP tools, call `relkit_project` with `request.action = "inspect"`
and the explicit absolute checkout `root`. This reads metadata without executing
the project's projection. Review its path, version, SHA-256 and policy inputs.
Then use action `bind`; let the client ask the human to trust that project code.
Never manufacture or automatically accept an elicitation response.

Pass the returned `binding` alongside `request` to every workflow tool. There is
no default project. A binding is process-local and expires on restart or reviewed
input drift. Re-inspect and bind again; never work around the refusal.

If MCP is unavailable, the project's reviewed `python .github/relkit.pyz` CLI
remains usable. Keep the existing project's required authorization gates. A
missing projection or version below 0.9.0 needs a separately reviewed CLI update
before plugin MCP can execute it.

## Choose the requested workflow

- Read/check: use version, audit, exposure, overlay, notes or protect check.
  Checks can provision project-local caches; they are not a zero-write sandbox.
- Updating the project copy, refreshing its guard or rolling back:
  read [updates.md](references/updates.md).
- Planning, publishing or resuming a release:
  read [releases.md](references/releases.md).

Treat `isError`, the structured CLI status and its exit code as authoritative;
do not infer success from an empty diagnostic stream. `restart_required` means
the old pin is no longer usable. Report retained scratch and recovery receipts.

Project trust is not permission to publish or change a Git hook. Installation
or updating of this plugin does not update project projections, approve a release,
install hooks or supersede project rules. The plugin version and project CLI
version can differ. Never bypass hooks, alter history, or commit unrelated changes.
