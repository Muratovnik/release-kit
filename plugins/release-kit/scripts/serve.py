"""Load only the packaged source snapshot, not an editable checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from releasekit_mcp.server import main

if __name__ == "__main__":
    raise SystemExit(main(["--plugin", "--bundle", str(Path(__file__).resolve().parents[1])]))
