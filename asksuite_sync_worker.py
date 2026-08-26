#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from src.asksuite_api import AsksuiteClient
from src.asksuite_sync import index_status, rebuild_index, simulate, update_index_delta
from src.shared import get_secret


def main():
    parser = argparse.ArgumentParser(description="Asksuite -> ClickUp (modo seguro)")
    parser.add_argument("--reindex", action="store_true", help="reconstrói o índice ClickUp completo")
    parser.add_argument("--body", default="{}", help="JSON enviado ao POST /v1/attendances")
    args = parser.parse_args()

    clickup_token = get_secret("clickup_token")
    api_key = get_secret("asksuite_api_key")
    access_token = get_secret("asksuite_access_token")
    if not clickup_token:
        raise SystemExit("Token ClickUp ausente.")
    if not api_key or not access_token:
        raise SystemExit("Asksuite não autenticada.")

    if args.reindex or not index_status()["complete"]:
        print("Construindo índice ClickUp...")
        print(rebuild_index(clickup_token, progress=print))
    else:
        print("Atualizando índice delta...")
        print(update_index_delta(clickup_token))

    body = json.loads(args.body)
    attendances, _ = AsksuiteClient(api_key=api_key, access_token=access_token).attendances(body)
    details, stats = simulate(attendances)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("Modo seguro: nenhuma escrita no ClickUp foi executada.")


if __name__ == "__main__":
    main()
