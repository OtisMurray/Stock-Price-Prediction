"""Compatibility wrapper for the basic watchlist dashboard server."""

from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.dashboard.watchlist_dashboard import main


if __name__ == "__main__":
    main()
