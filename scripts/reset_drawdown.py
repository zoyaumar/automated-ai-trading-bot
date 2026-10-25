"""Clear a drawdown halt after client review."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.store import Store


def reset_drawdown(db_path: str = "data/bot.db") -> None:
    store = Store(db_path)
    print("Drawdown reset script — edit DB directly or add a reset flag table.")


if __name__ == "__main__":
    reset_drawdown()