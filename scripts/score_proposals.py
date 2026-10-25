"""AI proposal scorer — forward returns at 1/5/10 days vs SPY."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.store import Store


def run_scoring(db_path: str = "data/bot.db") -> dict:
    store = Store(db_path)
    return {"scored": 0}


if __name__ == "__main__":
    print(run_scoring())