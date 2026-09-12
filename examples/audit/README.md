# Minimal audit policy

These are the exact files used by the [CLI quick start](../../README.md#first-check-in-an-existing-project).
Copy `relkit.toml` and `.betterleaks.toml` to the target Git root and merge `.cache/`
into its `.gitignore`; do not overwrite an existing policy or ignore file blindly.
Place a reviewed `relkit.pyz` at `.github/relkit.pyz` as described in that guide.
Before the first audit, create the ignored directory explicitly:

```text
python -c "from pathlib import Path; Path('.cache').mkdir(exist_ok=True)"
```

Both native scanners stay enabled. No private policy, overlay, Git hook, release
configuration or MCP client is required. Run from the target project's root, not
from this examples directory. The full distribution check uses these same files
in a disposable adopter and exercises real worktree, staged and history audits.
