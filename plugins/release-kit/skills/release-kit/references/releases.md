# Managing a release

Use the project's declared release configuration and commands. A repository with
audit/notes/protect configuration alone is not automatically ready for release
orchestration. Do not invent platform lists, signing policies or smoke commands.

## Choose the checkout and check prerequisites

Use the existing ordinary checkout by default; a new local clone or hosted
repository is not a release prerequisite. Check Git status, the configured
release commands, required guard and active development processes before long
checks. A required missing guard uses `relkit_protect` plan/install: review the
hook path, existing bytes and dispatcher. If the direct request covers this
necessary owned-hook installation and project policy permits it, pass
`plan_hash = result.data.plan.plan_sha256` and
`authorization = {"source": "user_request", "scope": "protect_install",
"review_sha256": <protect plan response hash>}`. Otherwise obtain only the
missing hook authority. Never replace a foreign hook or change a dispatcher.
After hook changes, inspect and rebind before other workflows.

Isolation is conditional: project checks may overwrite a running dev server's
generated files. Use a supported project-local output isolation mechanism, or a
task-owned local checkout when needed; do not stop someone else's server. Choose
the release execution checkout before checks: `run` executes configured commands
there too. If it must differ, account for its exact commit, dependencies, hooks
and recovery state before using it. Linked worktrees are currently unsupported.
Keep scratch inside the project; an external path needs explicit agreement.
Do not change ownership or global Git trust to make a clone work. Diagnose a
Git ownership error against the exact intended repository before any exception.

## Prepare and publish

1. Validate the requested changelog entry with `relkit_notes` without an output
   file unless an export is needed. Keep structural validation separate from Git
   boundary checks. Follow project-required pre-commit gates and commit only
   authorized changes; `audit --history` needs a clean checkout, while `--staged`
   checks the prepared index. The coordinator does not create that commit. Before
   validation, curate notes for the released product's users: omit adopter-internal
   release machinery, tool names/versions and process-only changes unless they
   materially change installation, updating, compatibility or product use. This
   distinction does not hide the name of release-kit when release-kit itself is the
   product. In ordinary sections, keep a matching commit link on the same Markdown
   source line as the opening bullet; do not hard-wrap it onto an indented line.
2. Request `relkit_release` action `plan` with the exact version. Review commit
   SHA, previous tag, local checks, intended refs, CI workflow and artifact policy.
   Planning does not authorize server-side changes. Let `run` perform the
   configured release checks; do not manually repeat the entire sequence before
   it just to obtain another green report. Separately required commit gates and
   CI remain independent; an earlier manual run is not a reusable release receipt.
3. For a user-authorized publication, call action `run` with version and the
   returned `result.data.plan.plan_sha256` as `plan_hash`, retaining the preview's
   options. A direct release request that covers this exact plan uses
   `authorization = {"source": "user_request", "scope": "release_run",
   "review_sha256": <release plan response hash>}`. Do not request a second
   dialog for the same authorized effects. Without that authority, omit the field
   and use native confirmation. An update-only request does not authorize a release.
4. Read the structured result. A timeout/disconnect is an unknown remote outcome,
   not proof that the tag was not pushed. First request `status`; it reads a local
   receipt, not fresh remote evidence. Use `resume_plan` with the desired resume
   options to review the saved plan and get its separate authorization hash.
   Authorized continuation uses `resume`, the saved plan hash and scope
   `release_resume` with that preview's `review_sha256`. A changed
   `accept_ci_attempt` needs its own explicit authority and matching preview.
   The coordinator reconciles remote state before further publication actions.
   If the attempt will never publish (for example CI rejected the tag and the
   owner then deleted it from the remote), record that with the project CLI:
   `release abandon vX.Y.Z --reason "..."`; MCP has no tool for it. The next
   `run` for that version archives the old receipt instead of refusing. Never
   delete refs or receipts to reuse a version.

The request authorizes effects, not every future plan. Review changed targets,
refs, policy and CI attempts; request new authority only if the effects exceed
the user's instruction. A later human refusal remains binding. A declined MCP
attempt is not retried through another route without new user direction.

## Verify an existing publication

Use `relkit_release` action `verify` (CLI: `relkit release verify VERSION`) when
publication exists but verification failed or needs repeating. It uses the saved
plan and original tag/commit/run identities, downloads the published files and
executes the pinned smoke commands. It creates no release or tag and needs no
publication authorization; it still has local execution and diagnostic effects.
Compatible old receipts keep their plan fingerprint. A different CI attempt is
not silently accepted. Inspect `tag_state`, `publication_state`, `ci_verdict`,
`verification` and `acceptance` separately. Verified artifacts with failed CI
return failure and incomplete acceptance; review the CI failure before resuming.

Never force-push, rewrite an existing release/tag, bypass Git hooks, auto-commit
unrelated changes, or lower local/CI trust gates. New local edits during CI waiting
are not the state of the published commit. Do not rerun a completed publication.
Keep failure diagnostics and resumable state where the tool reports them; clean
only owned disposable files. Disposable hosted repositories are required for live
tests of release-kit's publishing machinery, not for ordinary user-requested
releases of an existing project. Never treat a local test as hosted acceptance.
