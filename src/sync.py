from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import settings
from .db import initialize_db


@dataclass
class SyncReport:
    started_at: str
    finished_at: str
    status: str
    message: str


def run_daily_sync() -> SyncReport:
    started = datetime.now(timezone.utc)
    initialize_db()

    # V1: estrutura pronta para encaixar:
    # 1) leitura incremental de leads
    # 2) match por telefone/e-mail
    # 3) atualização de PMS/conversão
    # 4) atualização ClickUp sem duplicar
    # 5) reconciliação de jornada
    mode = "DRY RUN" if settings.sync_dry_run else "LIVE"

    finished = datetime.now(timezone.utc)
    return SyncReport(
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        status="ok",
        message=f"Sync base inicial executado em modo {mode}.",
    )
