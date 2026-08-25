from __future__ import annotations

import json

from src.sync import run_daily_sync


if __name__ == "__main__":
    report = run_daily_sync()
    print(json.dumps(report.__dict__, ensure_ascii=False), flush=True)
