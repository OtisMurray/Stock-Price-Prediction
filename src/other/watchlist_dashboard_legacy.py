from __future__ import annotations

import os
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

venv_python = repo_root / ".venv" / "bin" / "python"
current_python = Path(sys.executable)
if (
    venv_python.exists()
    and current_python != venv_python
    and os.environ.get("STOCK_DASHBOARD_VENV_BOOTSTRAPPED") != "1"
):
    env = dict(os.environ)
    env["STOCK_DASHBOARD_VENV_BOOTSTRAPPED"] = "1"
    os.execve(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]], env)

from src.dashboard.watchlist_dashboard_legacy import main


if __name__ == "__main__":
    main()
