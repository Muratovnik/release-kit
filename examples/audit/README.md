# Minimal audit policy

These are the exact files used by the [CLI quick start](../../README.md#first-check-in-an-existing-project).
Copy `relkit.toml` to the target Git root and merge `.cache/` into its `.gitignore`; do
not overwrite an existing policy or ignore file blindly. `.betterleaks.toml` is optional:
without it Betterleaks scans with its maintained default rules, which is what this file
selects explicitly. Copy it only to have a place to pin `betterleaksMinVersion` or add a
narrow allowlist. For a project that also drafts release notes, see
[examples/minimal](../minimal/relkit.toml).
Place a reviewed `relkit.pyz` at `.github/relkit.pyz` as described in that guide.
Before the first audit, create the ignored directory explicitly:

```text
python -c "from pathlib import Path; Path('.cache').mkdir(exist_ok=True)"
```

Both native scanners stay enabled. No private policy, overlay, Git hook, release
configuration is required. Run from the target project's root, not
from this examples directory. The full distribution check uses these same files
in a disposable adopter and exercises real worktree, staged and history audits.
