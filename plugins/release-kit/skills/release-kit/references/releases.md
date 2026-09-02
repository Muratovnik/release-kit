# Managing a release

Use the project's declared release configuration and commands. A repository with
audit/notes/protect configuration alone is not automatically ready for release
orchestration. Do not invent platform lists, signing policies or smoke commands.

1. Validate the requested changelog entry with `relkit_notes`. Keep structural
   notes validation separate from Git boundary checks in the release coordinator.
2. Request `relkit_release` action `plan` with the exact version. Review commit
   SHA, previous tag, local checks, intended refs, CI workflow and artifact policy.
   Planning does not authorize server-side changes.
3. For a user-authorized publication, call action `run` with version and the
   returned `plan_sha256` as `plan_hash`. The client's native confirmation must
   show the actual operation. Do not auto-approve it.
4. Read the structured result. A timeout/disconnect is an unknown remote outcome,
   not proof that the tag was not pushed. First request `status`; it reads a local
   receipt, not fresh remote evidence. Review the saved plan and resume with its
   version/hash only after authorization. The coordinator reconciles remote state.

Never force-push, rewrite an existing release/tag, bypass Git hooks, auto-commit
unrelated changes, or lower local/CI trust gates. New local edits during CI waiting
are not the state of the published commit. Do not rerun a completed publication.
Keep failure diagnostics and resumable state where the tool reports them; clean
only owned disposable files. Real publication tests need an agreed test repository.
