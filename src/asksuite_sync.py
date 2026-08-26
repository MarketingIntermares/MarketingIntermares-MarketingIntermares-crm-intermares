from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from src.shared import DB_NAMESPACE, db_query, get_secret, kv_get, kv_set

LIST_COMERCIAL = "901715970646"
LIST_BASE2 = "901716142212"
LIST_POSVENDAS = "901714375528"
LISTAS_DE_BUSCA = [LIST_COMERCIAL, LIST_BASE2, LIST_POSVENDAS]

STATUS_COMERCIAL = "em atendimento"
STATUS_POSVENDAS = "cliente recebido"

CLICKUP_BASE = "https://api.clickup.com/api/v2"
SPACE_ID = "90175790679"

CAMPOS = {
    "WHATSAPP": "5ceea458-2f40-46dd-aed7-b4ee7d906f74",
    "TELEFONE": "87931c2e-7eed-4edd-84e2-f6da07af85e8",
    "TELEFONE2": "7478a617-bbc3-49fc-aafe-e5df43b3ed2e",
    "EMAIL": "fb2c68ca-0142-438f-8894-d51d8e5f4fee",
    "CONVERSA": "cd04177c-217d-4bfd-b9ad-79ded537605d",
}

RANK = {
    "em fluxo": 1, "apto para wpp": 1, "apto somente e-mail": 1,
    "apto para wpp + e-mail": 1, "retornado ao pool": 1,
    "em atendimento": 2, "qualificação": 3, "apresentação proposta": 4,
    "em negociação": 5, "fechamento": 6, "ganho": 7, "onboarding": 8,
    "membro ativado": 9,
    "cliente recebido": 1, "ativação": 2, "acompanhamento": 3,
    "relacionamento": 4, "cliente ativo": 5, "reengajado": 6,
}
TERMINAIS = {
    "perdido", "perdido sem contato", "inválido/bloqueado",
    "reativação futura", "inativos", "risco / recuperação", "complete",
}

T_MEMBRO = {"sou membro", "[pv] concluído", "[pv] tratativa", "[pv] rci", "[pv] hospede_futuro"}
T_FIN = {"financeiro", "tag - financeiro", "renegociação de parcelas", "solicitar fatura", "alteração de vencimento da fatura"}
T_SAC = {"sac", "tag - sac", "cancelamento"}

INDEX_TABLE = f"{DB_NAMESPACE}_clickup_contact_index"
SYNC_RUNS_TABLE = f"{DB_NAMESPACE}_asksuite_sync_runs"


def ensure_sync_schema() -> None:
    db_query(
        f"""CREATE TABLE IF NOT EXISTS {INDEX_TABLE} (
            match_key TEXT PRIMARY KEY,
            key_type TEXT NOT NULL,
            task_id TEXT NOT NULL,
            list_id TEXT,
            status TEXT,
            has_owner BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        fetch=None,
    )
    db_query(
        f"""CREATE INDEX IF NOT EXISTS {INDEX_TABLE}_task_idx
        ON {INDEX_TABLE}(task_id)""",
        fetch=None,
    )
    db_query(
        f"""CREATE TABLE IF NOT EXISTS {SYNC_RUNS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            mode TEXT NOT NULL,
            attendances INTEGER NOT NULL DEFAULT 0,
            identified INTEGER NOT NULL DEFAULT 0,
            matched INTEGER NOT NULL DEFAULT 0,
            new_cards INTEGER NOT NULL DEFAULT 0,
            commercial INTEGER NOT NULL DEFAULT 0,
            post_sales INTEGER NOT NULL DEFAULT 0,
            discarded INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            details_json JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        fetch=None,
    )


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip().lower()


def norm_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-11:] if len(digits) >= 10 else ""


def norm_email(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if "@" in text and not set(text) <= {"-"} else ""


def deep_get(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def normalize_attendance(raw: dict) -> dict:
    attendance_id = deep_get(raw, ["id", "attendanceId", "_id", "attendance.id"])
    name = deep_get(raw, [
        "contact.name", "customer.name", "guest.name", "lead.name",
        "contactName", "name", "user.name",
    ])
    email = deep_get(raw, [
        "contact.email", "customer.email", "guest.email", "lead.email",
        "contactEmail", "email", "user.email",
    ])
    phone = deep_get(raw, [
        "contact.phone", "contact.whatsapp", "customer.phone", "customer.whatsapp",
        "guest.phone", "lead.phone", "phone", "whatsapp", "contactPhone",
    ])
    stage = deep_get(raw, ["stage.name", "stage", "funnelStage.name", "attendanceStage"])
    status = deep_get(raw, ["status.name", "status", "conversationStatus"])
    attendant = deep_get(raw, ["attendant.name", "agent.name", "responsible.name", "assignedTo.name"])
    updated_at = deep_get(raw, ["updatedAt", "updated_at", "lastMessageAt", "lastInteractionAt"])
    company = deep_get(raw, ["company.slug", "company.id", "company.name", "company"])
    url = deep_get(raw, ["url", "conversationUrl", "attendanceUrl", "link"])

    tags_raw = deep_get(raw, ["tags", "labels", "tagNames"]) or []
    tags = []
    if isinstance(tags_raw, list):
        for item in tags_raw:
            if isinstance(item, dict):
                label = item.get("name") or item.get("label") or item.get("title")
            else:
                label = item
            if label:
                tags.append(str(label).strip())

    return {
        "attendance_id": str(attendance_id or ""),
        "name": str(name or "").strip(),
        "email": norm_email(email),
        "phone": norm_phone(phone),
        "stage": str(stage or "").strip(),
        "status": str(status or "").strip(),
        "attendant": str(attendant or "").strip(),
        "updated_at": str(updated_at or ""),
        "company": str(company or ""),
        "conversation_url": str(url or ""),
        "tags": tags,
        "raw": raw,
    }


def route(att: dict) -> dict:
    tags_norm = {norm_text(x) for x in att.get("tags", [])}
    if not att["phone"] and not att["email"]:
        return {"destination": "DESCARTE", "reason": "sem telefone e sem e-mail"}
    if "[mkt] mencao" in tags_norm or norm_text(att.get("stage")) == "mencoes do instagram":
        return {"destination": "DESCARTE", "reason": "menção de Instagram"}
    if tags_norm & (T_MEMBRO | T_FIN | T_SAC):
        return {
            "destination": "POSVENDAS",
            "target_list": LIST_POSVENDAS,
            "target_status": STATUS_POSVENDAS,
            "reason": "membro, financeiro ou SAC",
        }
    return {
        "destination": "COMERCIAL",
        "target_list": LIST_COMERCIAL,
        "target_status": STATUS_COMERCIAL,
        "reason": f"etapa {att.get('stage') or 'sem etapa'}",
    }


def clickup_request(token: str, method: str, path: str, **kwargs) -> dict:
    for attempt in range(6):
        response = requests.request(
            method,
            f"{CLICKUP_BASE}{path}",
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=90,
            **kwargs,
        )
        if response.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        if response.status_code >= 500:
            time.sleep(2 + attempt * 3)
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"ClickUp {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}
    raise RuntimeError("ClickUp indisponível após múltiplas tentativas")


def _field_value(task: dict, field_ids: set[str]) -> list[str]:
    values = []
    for cf in task.get("custom_fields", []):
        if str(cf.get("id")) in field_ids:
            value = cf.get("value")
            if isinstance(value, (str, int, float)):
                values.append(str(value))
    return values


def task_keys(task: dict) -> tuple[set[str], set[str]]:
    values = [str(task.get("name") or "")] + _field_value(task, set(CAMPOS.values()))
    phones, emails = set(), set()
    for value in values:
        p = norm_phone(value)
        e = norm_email(value)
        if p:
            phones.add(p)
        if e:
            emails.add(e)
    return phones, emails


def upsert_index_tasks(tasks: list[dict]) -> int:
    count = 0
    for task in tasks:
        phones, emails = task_keys(task)
        status = norm_text((task.get("status") or {}).get("status"))
        list_id = str((task.get("list") or {}).get("id") or "")
        has_owner = bool(task.get("assignees"))
        for key_type, values in (("phone", phones), ("email", emails)):
            for value in values:
                db_query(
                    f"""INSERT INTO {INDEX_TABLE}(match_key,key_type,task_id,list_id,status,has_owner,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT(match_key) DO UPDATE SET
                    key_type=EXCLUDED.key_type, task_id=EXCLUDED.task_id,
                    list_id=EXCLUDED.list_id, status=EXCLUDED.status,
                    has_owner=EXCLUDED.has_owner, updated_at=NOW()""",
                    (value, key_type, str(task["id"]), list_id, status, has_owner),
                    fetch=None,
                )
                count += 1
    return count


def _paginate(token: str, list_id: str, params: dict) -> list[dict]:
    output = []
    page = 0
    while True:
        query = {"page": page, "include_closed": "true", "subtasks": "true", **params}
        data = clickup_request(token, "GET", f"/list/{list_id}/task", params=query)
        batch = data.get("tasks", [])
        output.extend(batch)
        if data.get("last_page") is True or not batch or len(batch) < 100:
            break
        page += 1
        if page >= 990:
            raise RuntimeError("Limite de paginação atingido; índice marcado como incompleto")
    return output


def rebuild_index(token: str, progress: Callable[[str], None] | None = None) -> dict:
    ensure_sync_schema()
    db_query(f"DELETE FROM {INDEX_TABLE}", fetch=None)
    start_year = 2018
    now = datetime.now(timezone.utc)
    windows = []
    year, month = start_year, 1
    while (year, month) <= (now.year, now.month):
        a = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            b = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            year, month = year + 1, 1
        else:
            b = datetime(year, month + 1, 1, tzinfo=timezone.utc)
            month += 1
        windows.append((int(a.timestamp() * 1000), int(b.timestamp() * 1000), a.strftime("%m/%Y")))

    tasks_read = 0
    try:
        for list_id in LISTAS_DE_BUSCA:
            for a, b, label in windows:
                if progress:
                    progress(f"Lista {list_id} · {label}")
                batch = _paginate(token, list_id, {"date_created_gt": a, "date_created_lt": b})
                tasks_read += len(batch)
                upsert_index_tasks(batch)
        kv_set("asksuite_clickup_index_complete", "true")
        kv_set("asksuite_clickup_index_updated_at", str(int(datetime.now(timezone.utc).timestamp() * 1000)))
        return {"complete": True, "tasks_read": tasks_read}
    except Exception:
        kv_set("asksuite_clickup_index_complete", "false")
        raise


def update_index_delta(token: str) -> dict:
    ensure_sync_schema()
    if kv_get("asksuite_clickup_index_complete") != "true":
        return {"complete": False, "tasks_read": 0}
    since = int(kv_get("asksuite_clickup_index_updated_at") or 0)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    tasks_read = 0
    try:
        for list_id in LISTAS_DE_BUSCA:
            batch = _paginate(token, list_id, {"date_updated_gt": since})
            tasks_read += len(batch)
            upsert_index_tasks(batch)
        kv_set("asksuite_clickup_index_updated_at", str(now_ms))
        return {"complete": True, "tasks_read": tasks_read}
    except Exception:
        kv_set("asksuite_clickup_index_complete", "false")
        raise


def index_status() -> dict:
    ensure_sync_schema()
    row = db_query(f"SELECT COUNT(*), COUNT(DISTINCT task_id) FROM {INDEX_TABLE}", fetch="one")
    return {
        "complete": kv_get("asksuite_clickup_index_complete") == "true",
        "keys": int(row[0] if row else 0),
        "tasks": int(row[1] if row else 0),
        "updated_at": kv_get("asksuite_clickup_index_updated_at"),
    }


def find_card(att: dict) -> dict | None:
    if att.get("email"):
        row = db_query(
            f"SELECT task_id,list_id,status,has_owner FROM {INDEX_TABLE} WHERE match_key=%s",
            (att["email"],), "one",
        )
        if row:
            return {"task_id": row[0], "list_id": row[1], "status": row[2], "has_owner": row[3], "matched_by": "email"}
    if att.get("phone"):
        row = db_query(
            f"SELECT task_id,list_id,status,has_owner FROM {INDEX_TABLE} WHERE match_key=%s",
            (att["phone"],), "one",
        )
        if row:
            return {"task_id": row[0], "list_id": row[1], "status": row[2], "has_owner": row[3], "matched_by": "phone"}
    return None


def simulate(attendances: list[dict]) -> tuple[list[dict], dict]:
    ensure_sync_schema()
    if kv_get("asksuite_clickup_index_complete") != "true":
        raise RuntimeError("Índice ClickUp incompleto. A simulação não pode classificar criação/duplicidade com segurança.")
    details = []
    stats = {
        "attendances": len(attendances), "identified": 0, "matched": 0, "new_cards": 0,
        "commercial": 0, "post_sales": 0, "discarded": 0, "errors": 0,
    }
    for raw in attendances:
        att = normalize_attendance(raw)
        route_info = route(att)
        if att["phone"] or att["email"]:
            stats["identified"] += 1
        if route_info["destination"] == "DESCARTE":
            stats["discarded"] += 1
            details.append({**att, **route_info, "task_id": "", "current_status": "", "action": "DESCARTAR"})
            continue
        if route_info["destination"] == "COMERCIAL":
            stats["commercial"] += 1
        else:
            stats["post_sales"] += 1
        card = find_card(att)
        if card:
            stats["matched"] += 1
            current = norm_text(card["status"])
            target = norm_text(route_info["target_status"])
            if current in TERMINAIS:
                action = "MANTER — status terminal"
            elif RANK.get(current, -1) >= RANK.get(target, -1):
                action = "MANTER — card igual/mais avançado"
            else:
                action = "ATUALIZAR STATUS"
            details.append({
                **att, **route_info, "task_id": card["task_id"],
                "current_status": current, "matched_by": card["matched_by"], "action": action,
            })
        else:
            stats["new_cards"] += 1
            details.append({**att, **route_info, "task_id": "", "current_status": "", "matched_by": "", "action": "SERIA CRIADO"})
    db_query(
        f"""INSERT INTO {SYNC_RUNS_TABLE}(mode,attendances,identified,matched,new_cards,commercial,post_sales,discarded,errors,details_json)
        VALUES('SAFE',%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            stats["attendances"], stats["identified"], stats["matched"], stats["new_cards"],
            stats["commercial"], stats["post_sales"], stats["discarded"], stats["errors"],
            json.dumps([{k: v for k, v in d.items() if k != "raw"} for d in details], ensure_ascii=False),
        ),
        fetch=None,
    )
    return details, stats
