"""Compatibility wrapper for the ticker collection runner."""

from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.runners.collect_all_for_ticker import collect_for_ticker, main


if __name__ == "__main__":
    main()
