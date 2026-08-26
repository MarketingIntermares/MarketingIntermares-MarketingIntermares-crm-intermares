import base64
import csv
import hashlib
import io
import os
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pandas as pd
import psycopg
import requests
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from src.shared import (
    APP_SECRET_KEY,
    CAMPAIGN_RUNS_TABLE,
    SECRETS_TABLE,
    SESSIONS_TABLE,
    USERS_TABLE,
    db_query,
    fernet,
    get_secret,
    init_schema,
    save_secret,
)


st.set_page_config(page_title="CRM Intermares", page_icon="IM", layout="wide")

WORKSPACE_ID = os.getenv("CLICKUP_WORKSPACE_ID", "90171207900")
SOURCE_LISTS = {
    "BASE DE LEADS": "901715970646",
    "BASE DE LEADS 2": "901716142212",
}
MEMBERS_LIST_ID = "901713986437"
DESTINATIONS = {
    "PROGRAMA DE MEMBROS": "901713986437",
    "B2C": "901713985675",
    "B2B": "901713986418",
    "SAC": "901714375550",
    "DAY USE": "901713986452",
    "PÓS VENDAS HOSPEDAGEM": "901716030222",
    "PÓS VENDAS CLUBE DE FÉRIAS": "901714375528",
}
ALLOWED_SOURCE_STATUSES = ["apto para wpp", "apto para wpp + e-mail"]

st.markdown("""
<style>
  .stApp { background:#f4f6f4; color:#162a34; }
  [data-testid="stSidebar"] { background:#0d2331; }
  [data-testid="stSidebar"] * { color:#e8f2f2; }
  .crm-title {font-size:1.65rem;font-weight:760;margin:0;letter-spacing:-.03em}
  .crm-kicker {font-size:.68rem;font-weight:800;letter-spacing:.12em;color:#0d8d85;text-transform:uppercase}
  .crm-card {background:white;border:1px solid #dbe3e4;border-radius:13px;padding:1.15rem;margin:.5rem 0 1rem}
  .crm-alert {background:#fff7ef;border:1px solid #efdbc8;border-radius:9px;padding:.8rem;color:#7d5235}
  .crm-safe {background:#e8f5f2;border:1px solid #cce6e2;border-radius:9px;padding:.8rem;color:#205c58}
  .crm-metric {background:#fff;border:1px solid #dbe3e4;border-radius:10px;padding:.8rem}
  div.stButton > button[kind="primary"] {background:#0d8d85;border-color:#0d8d85}
  .small-note {font-size:.78rem;color:#667780}
</style>
""", unsafe_allow_html=True)


def norm(value):
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(char for char in text if unicodedata.category(char) != "Mn").strip().lower()



def init_database():
    init_schema()
    admin_user = os.getenv("APP_USERNAME", "admin").strip() or "admin"
    admin_password = os.getenv("APP_PASSWORD", "").strip()
    exists = db_query(f"SELECT id FROM {USERS_TABLE} WHERE username=%s", (admin_user,), "one")
    if not exists and admin_password:
        db_query(
            f"INSERT INTO {USERS_TABLE}(username,password_hash,role) VALUES(%s,%s,'admin')",
            (admin_user, hash_password(admin_password)),
            fetch=None,
        )

def hash_password(password):
    salt = secrets.token_bytes(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password, encoded):
    try:
        iterations_text, salt_text, digest_text = encoded.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt_text), int(iterations_text))
        return secrets.compare_digest(base64.urlsafe_b64encode(digest).decode(), digest_text)
    except Exception:
        return False



def save_clickup_token(token, username):
    save_secret("clickup_token", token, username)


def get_clickup_token():
    return get_secret("clickup_token")

def create_session(user_id):
    raw = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=14)
    db_query(f"DELETE FROM {SESSIONS_TABLE} WHERE expires_at < NOW()", fetch=None)
    db_query(f"INSERT INTO {SESSIONS_TABLE}(token_hash,user_id,expires_at) VALUES(%s,%s,%s)", (token_hash, user_id, expires), fetch=None)
    return raw, expires


def user_from_session(raw):
    if not raw:
        return None
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return db_query(
        f"""SELECT u.id,u.username,u.role FROM {SESSIONS_TABLE} s JOIN {USERS_TABLE} u ON u.id=s.user_id
        WHERE s.token_hash=%s AND s.expires_at>NOW() AND u.active=TRUE""",
        (token_hash,), "one",
    )


def clickup_request(token, method, path, **kwargs):
    response = requests.request(
        method, f"https://api.clickup.com/api/v2{path}",
        headers={"Authorization": token, "Content-Type": "application/json"}, timeout=45, **kwargs,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"ClickUp {response.status_code}: {response.text[:180]}")
    return response.json() if response.content else {}


def list_fields(token, list_id):
    return clickup_request(token, "GET", f"/list/{list_id}/field").get("fields", [])


def option_label(field, raw):
    values = raw if isinstance(raw, list) else [raw]
    options = field.get("type_config", {}).get("options", [])
    labels = []
    for value in values:
        option = next((item for item in options if str(item.get("id")) == str(value)), None)
        labels.append((option or {}).get("name") or (option or {}).get("label") or str(value or ""))
    return ", ".join(labels)


def decoded_value(field):
    raw = field.get("value")
    if raw in (None, ""):
        return ""
    if field.get("type") in ("drop_down", "labels"):
        return option_label(field, raw)
    if isinstance(raw, dict):
        return str(raw)
    return raw


def task_field(task, names):
    wanted = [norm(item) for item in names]
    for field in task.get("custom_fields", []):
        field_name = norm(field.get("name"))
        if any(term == field_name or term in field_name for term in wanted):
            return decoded_value(field)
    return ""


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit() and len(str(value)) >= 10:
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        return datetime.fromtimestamp(number, timezone.utc).date()
    text = str(value).strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            pass
    return None


def stay_stage(task):
    pms = task_field(task, ["pms", "codigo pms", "código pms", "reserva pms"])
    checkin = parse_date(task_field(task, ["check-in", "check in", "checkin", "data de entrada"]))
    checkout = parse_date(task_field(task, ["check-out", "check out", "checkout", "data de saída", "data de saida"]))
    today = date.today()
    if not str(pms or "").strip():
        return "Somente cotação"
    if checkin and checkout and checkin <= today <= checkout:
        return "Hospedado agora"
    if checkout and checkout < today:
        return "Já se hospedou"
    if checkin and checkin > today:
        return "Vai se hospedar"
    return "Reserva com PMS sem datas válidas"


def task_phone(task):
    preferred_ids = {"5ceea458-2f40-46dd-aed7-b4ee7d906f74", "87931c2e-7eed-4edd-84e2-f6da07af85e8"}
    fields = task.get("custom_fields", [])
    field = next((item for item in fields if item.get("id") in preferred_ids), None)
    if not field:
        field = next((item for item in fields if norm(item.get("name")) in ("whatsapp", "telefone", "celular", "telefone 1")), None)
    digits = re.sub(r"\D", "", str(decoded_value(field) if field else ""))
    return digits if len(digits) >= 10 else ""


def task_name(task):
    value = task_field(task, ["nome", "nome do viajante", "viajante"])
    return str(value or task.get("name") or "Sem nome").strip()


def fetch_all_tasks(token, list_id, statuses=None, max_pages=100):
    output = []
    for page in range(max_pages):
        params = [("page", page), ("include_closed", "true"), ("subtasks", "false")]
        for status in statuses or []:
            params.append(("statuses[]", status))
        data = clickup_request(token, "GET", f"/list/{list_id}/task", params=params)
        batch = data.get("tasks", [])
        output.extend(batch)
        if len(batch) < 100:
            break
    return output


def field_catalog(token):
    catalog = {}
    for list_name, list_id in SOURCE_LISTS.items():
        for field in list_fields(token, list_id):
            key = f"{norm(field.get('name'))}::{field.get('type')}"
            item = catalog.setdefault(key, {"name": field.get("name"), "type": field.get("type"), "options": set(), "lists": []})
            item["lists"].append(list_name)
            for option in field.get("type_config", {}).get("options", []):
                label = option.get("name") or option.get("label")
                if label:
                    item["options"].add(label)
    return {key: {**value, "options": sorted(value["options"])} for key, value in sorted(catalog.items(), key=lambda row: row[1]["name"])}


def task_matches_rules(task, rules):
    for rule in rules:
        value = task_field(task, [rule["name"]])
        expected = rule.get("value", "")
        operator = rule["operator"]
        if operator == "Está preenchido" and value in (None, ""):
            return False
        if operator == "Não está preenchido" and value not in (None, ""):
            return False
        if operator == "É igual a" and norm(value) != norm(expected):
            return False
        if operator == "É diferente de" and norm(value) == norm(expected):
            return False
        if operator == "Contém" and norm(expected) not in norm(value):
            return False
        if operator in ("Maior que", "Menor que"):
            try:
                if operator == "Maior que" and not float(value) > float(expected):
                    return False
                if operator == "Menor que" and not float(value) < float(expected):
                    return False
            except (TypeError, ValueError):
                return False
    return True


def task_has_department_conflict(task):
    destination_ids = set(DESTINATIONS.values())
    locations = task.get("locations") or []
    for location in locations:
        list_id = str(location.get("list_id") or location.get("id") or "")
        if list_id in destination_ids:
            return True
    return False


def list_members(token, list_id):
    data = clickup_request(token, "GET", f"/list/{list_id}/member")
    raw = data if isinstance(data, list) else data.get("members", [])
    members = []
    for member in raw:
        user = member.get("user", member)
        if user.get("id"):
            members.append({"id": str(user["id"]), "name": user.get("username") or user.get("email") or str(user["id"])})
    return sorted(members, key=lambda item: item["name"].lower())


def build_csv(rows, sellers):
    stream = io.StringIO()
    columns = ["PHONE", "nomeDoViajante"] + (["nomeDoVendedor"] if sellers else [])
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for index, row in enumerate(rows):
        output = {"PHONE": row["phone"], "nomeDoViajante": row["name"]}
        if sellers:
            output["nomeDoVendedor"] = sellers[index % len(sellers)]["name"]
        writer.writerow(output)
    return stream.getvalue().encode("utf-8-sig")


def execute_campaign(token, rows, destination_id, tag, sellers, status, progress):
    results = []
    for index, row in enumerate(rows):
        seller = sellers[index % len(sellers)] if sellers else None
        errors = []
        try:
            clickup_request(token, "POST", f"/task/{row['id']}/tag/{quote(tag, safe='')}")
        except Exception as exc:
            errors.append(f"tag: {exc}")
        try:
            clickup_request(token, "POST", f"/list/{destination_id}/task/{row['id']}")
        except Exception as exc:
            if "already" not in str(exc).lower():
                errors.append(f"lista: {exc}")
        body = {"status": status}
        if seller:
            body["assignees"] = {"add": [int(seller["id"])]}
        try:
            clickup_request(token, "PUT", f"/task/{row['id']}", json=body)
        except Exception as exc:
            errors.append(f"responsável/status: {exc}")
        results.append({"id": row["id"], "ok": not errors, "errors": errors})
        progress.progress((index + 1) / len(rows), text=f"Atualizando {index + 1} de {len(rows)} leads")
    return results


try:
    init_database()
except Exception as exc:
    st.error(f"Não foi possível inicializar o banco de dados: {exc}")
    st.stop()

if not APP_SECRET_KEY:
    st.error("APP_SECRET_KEY não configurada no servidor.")
    st.stop()

cookies = EncryptedCookieManager(prefix="crm_intermares_", password=APP_SECRET_KEY)
if not cookies.ready():
    st.stop()

current_user = user_from_session(cookies.get("session"))

if not current_user:
    st.markdown('<div class="crm-kicker">Rede Intermares</div><div class="crm-title">CRM Intermares</div>', unsafe_allow_html=True)
    st.caption("Acesso independente para administração e operação comercial")
    with st.form("login"):
        username = st.text_input("Usuário")
        password = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar", type="primary", width="stretch")
    if submit:
        user = db_query(f"SELECT id,username,role,password_hash FROM {USERS_TABLE} WHERE username=%s AND active=TRUE", (username.strip(),), "one")
        if user and verify_password(password, user[3]):
            raw, expires = create_session(user[0])
            cookies["session"] = raw
            cookies.save()
            st.rerun()
        st.error("Usuário ou senha incorretos.")
    st.stop()

user_id, username, role = current_user

with st.sidebar:
    st.markdown("## CRM Intermares")
    st.caption(f"{username} · {role}")
    menu_options = ["Segmentação", "Minha senha"] + (["Usuários", "Configurações"] if role == "admin" else [])
    page = st.radio("Navegação", menu_options, label_visibility="collapsed")
    st.divider()
    if st.button("Sair", width="stretch"):
        cookies["session"] = ""
        cookies.save()
        st.rerun()

if page == "Minha senha":
    st.markdown('<div class="crm-kicker">Segurança</div><div class="crm-title">Alterar minha senha</div>', unsafe_allow_html=True)
    with st.form("change_password"):
        current_password = st.text_input("Senha atual", type="password")
        new_password = st.text_input("Nova senha", type="password")
        confirm_password = st.text_input("Confirmar nova senha", type="password")
        if st.form_submit_button("Atualizar senha", type="primary"):
            stored = db_query(f"SELECT password_hash FROM {USERS_TABLE} WHERE id=%s", (user_id,), "one")
            if not stored or not verify_password(current_password, stored[0]):
                st.error("A senha atual não confere.")
            elif len(new_password) < 8:
                st.error("A nova senha precisa ter pelo menos 8 caracteres.")
            elif new_password != confirm_password:
                st.error("A confirmação da nova senha está diferente.")
            else:
                db_query(f"UPDATE {USERS_TABLE} SET password_hash=%s WHERE id=%s", (hash_password(new_password), user_id), fetch=None)
                db_query(f"DELETE FROM {SESSIONS_TABLE} WHERE user_id=%s", (user_id,), fetch=None)
                cookies["session"] = ""
                cookies.save()
                st.success("Senha alterada. Entre novamente.")
                st.stop()
    st.stop()

if page == "Usuários":
    st.markdown('<div class="crm-kicker">Administração</div><div class="crm-title">Usuários e acessos</div>', unsafe_allow_html=True)
    with st.form("new_user"):
        col1, col2, col3 = st.columns([1, 1, .7])
        new_username = col1.text_input("Novo usuário")
        new_password = col2.text_input("Senha provisória", type="password")
        new_role = col3.selectbox("Nível", ["usuario", "admin"])
        if st.form_submit_button("Criar usuário", type="primary"):
            if len(new_username.strip()) < 3 or len(new_password) < 8:
                st.error("Use um usuário com 3 caracteres e senha com pelo menos 8 caracteres.")
            else:
                try:
                    db_query(f"INSERT INTO {USERS_TABLE}(username,password_hash,role) VALUES(%s,%s,%s)", (new_username.strip(), hash_password(new_password), new_role), fetch=None)
                    st.success("Usuário criado.")
                except Exception:
                    st.error("Esse nome de usuário já existe.")
    users = db_query(f"SELECT username,role,active,created_at FROM {USERS_TABLE} ORDER BY username")
    st.dataframe(pd.DataFrame(users, columns=["Usuário", "Nível", "Ativo", "Criado em"]), width="stretch", hide_index=True)
    st.stop()

if page == "Configurações":
    st.markdown('<div class="crm-kicker">Administração</div><div class="crm-title">Conexão com o ClickUp</div>', unsafe_allow_html=True)
    token_exists = bool(get_clickup_token())
    st.markdown(f'<div class="crm-safe"><b>{"Token salvo e protegido" if token_exists else "Token ainda não cadastrado"}</b><br><span class="small-note">A credencial é criptografada e não fica disponível para os usuários.</span></div>', unsafe_allow_html=True)
    with st.form("token_form"):
        new_token = st.text_input("Token pessoal novo", type="password")
        if st.form_submit_button("Validar e salvar", type="primary"):
            try:
                account = clickup_request(new_token.strip(), "GET", "/user")
                save_clickup_token(new_token, username)
                st.success(f"Conectado como {account.get('user', {}).get('username') or 'ClickUp'}.")
            except Exception as exc:
                st.error(f"Token não aceito: {exc}")
    st.stop()

st.markdown('<div class="crm-kicker">CRM · REDE INTERMARES</div><div class="crm-title">Segmentação de leads</div>', unsafe_allow_html=True)
st.caption("Selecione o público, confira conflitos e só depois execute a distribuição.")

try:
    token = get_clickup_token()
except Exception:
    token = ""
if not token:
    st.markdown('<div class="crm-alert"><b>ClickUp não conectado.</b><br>Peça ao administrador para cadastrar o token em Configurações.</div>', unsafe_allow_html=True)
    st.stop()

try:
    catalog = field_catalog(token)
except Exception as exc:
    st.error(f"Não foi possível ler os campos do ClickUp: {exc}")
    st.stop()

with st.expander("1. Público e situação da hospedagem", expanded=True):
    col1, col2 = st.columns([1.35, 1])
    stages = col1.multiselect(
        "Situação da reserva/hospedagem",
        ["Somente cotação", "Vai se hospedar", "Hospedado agora", "Já se hospedou", "Reserva com PMS sem datas válidas"],
        default=["Somente cotação"],
        help="Calculada pelo código PMS e pelas datas de check-in e check-out em relação à data atual.",
    )
    max_leads = col2.number_input("Quantidade máxima", min_value=1, max_value=1000, value=250)
    exclude_members = st.checkbox("Excluir quem já está no Programa de Membros", value=True)
    exclude_conflicts = st.checkbox("Excluir leads já adicionados a outro departamento", value=True, help="Previne campanhas e mensagens simultâneas entre departamentos.")
    if "Hospedado agora" in stages:
        st.warning("Atenção: hóspedes durante a estadia podem estar sendo atendidos por SAC ou Pós-vendas.")

with st.expander("2. Campos personalizados", expanded=True):
    field_keys = list(catalog.keys())
    number_rules = st.number_input("Quantidade de regras adicionais", min_value=0, max_value=8, value=0, step=1)
    rules = []
    for index in range(int(number_rules)):
        cols = st.columns([1.3, .8, 1])
        selected_key = cols[0].selectbox("Campo", field_keys, format_func=lambda key: catalog[key]["name"], key=f"field_{index}")
        operator = cols[1].selectbox("Condição", ["É igual a", "Contém", "É diferente de", "Maior que", "Menor que", "Está preenchido", "Não está preenchido"], key=f"op_{index}")
        definition = catalog[selected_key]
        if operator in ("Está preenchido", "Não está preenchido"):
            value = ""
            cols[2].text_input("Valor", value="Não necessário", disabled=True, key=f"disabled_{index}")
        elif definition["options"]:
            value = cols[2].selectbox("Valor", definition["options"], key=f"value_{index}")
        else:
            value = cols[2].text_input("Valor", key=f"value_{index}")
        rules.append({"name": definition["name"], "operator": operator, "value": value})

with st.expander("3. Campanha e responsáveis", expanded=True):
    col1, col2, col3 = st.columns([1.3, 1, .7])
    campaign_tag = col1.text_input("Tag da campanha", value="[CAM] CLUBE BEN BRINDE")
    destination_name = col2.selectbox("Lista de destino", list(DESTINATIONS.keys()))
    target_status = col3.selectbox("Status", ["em fluxo", "apto para wpp", "retornar ao pool"])
    try:
        members = list_members(token, DESTINATIONS[destination_name])
    except Exception:
        members = []
    member_map = {member["name"]: member for member in members}
    preferred = [name for name in member_map if any(term in norm(name) for term in ("tamara", "marcio", "márcio"))]
    selected_seller_names = st.multiselect("Vendedores responsáveis no ClickUp e no CSV", list(member_map.keys()), default=preferred)
    selected_sellers = [member_map[name] for name in selected_seller_names]
    if not members:
        st.info("Nenhum membro com acesso explícito à lista de destino foi encontrado. O administrador deve liberar os vendedores nessa lista do ClickUp.")

if st.button("Buscar e conferir público", type="primary", width="stretch"):
    if not stages:
        st.error("Selecione pelo menos uma situação de hospedagem.")
    else:
        with st.spinner("Lendo as duas bases e conferindo conflitos…"):
            member_phones = set()
            if exclude_members:
                member_phones = {task_phone(task) for task in fetch_all_tasks(token, MEMBERS_LIST_ID) if task_phone(task)}
            seen = set()
            selected = []
            stats = {"analisados": 0, "fora_do_perfil": 0, "membros": 0, "duplicados": 0, "conflitos": 0, "sem_telefone": 0}
            for source_name, source_id in SOURCE_LISTS.items():
                for task in fetch_all_tasks(token, source_id, ALLOWED_SOURCE_STATUSES):
                    stats["analisados"] += 1
                    stage = stay_stage(task)
                    if stage not in stages or not task_matches_rules(task, rules):
                        stats["fora_do_perfil"] += 1
                        continue
                    phone = task_phone(task)
                    if not phone:
                        stats["sem_telefone"] += 1
                        continue
                    if phone in member_phones:
                        stats["membros"] += 1
                        continue
                    if phone in seen:
                        stats["duplicados"] += 1
                        continue
                    if exclude_conflicts and task_has_department_conflict(task):
                        stats["conflitos"] += 1
                        continue
                    seen.add(phone)
                    selected.append({"id": task["id"], "name": task_name(task), "phone": phone, "source": source_name, "stage": stage})
            st.session_state["selection"] = selected[: int(max_leads)]
            st.session_state["selection_stats"] = stats
            st.session_state["selection_config"] = {"tag": campaign_tag, "destination": destination_name, "status": target_status, "stages": stages}

selection = st.session_state.get("selection", [])
if selection:
    stats = st.session_state.get("selection_stats", {})
    st.markdown("### Resultado da segmentação")
    metric_columns = st.columns(6)
    labels = [("Selecionados", len(selection)), ("Analisados", stats.get("analisados", 0)), ("Membros", stats.get("membros", 0)), ("Duplicados", stats.get("duplicados", 0)), ("Conflitos", stats.get("conflitos", 0)), ("Sem telefone", stats.get("sem_telefone", 0))]
    for column, (label, value) in zip(metric_columns, labels):
        column.metric(label, value)
    preview = pd.DataFrame([{**row, "phone": f"{row['phone'][:2]}••••••{row['phone'][-4:]}"} for row in selection[:30]])
    st.dataframe(preview.rename(columns={"name": "Viajante", "phone": "Telefone", "source": "Origem", "stage": "Situação"})[["Viajante", "Telefone", "Origem", "Situação"]], width="stretch", hide_index=True)
    csv_bytes = build_csv(selection, selected_sellers)
    st.download_button("Baixar CSV da campanha", csv_bytes, file_name=f"{re.sub(r'[^a-zA-Z0-9]+', '_', campaign_tag).strip('_').lower() or 'campanha'}.csv", mime="text/csv", width="stretch")
    st.divider()
    confirmation = st.checkbox("Conferi o público e autorizo atualizar os cards no ClickUp")
    if st.button("Executar campanha no ClickUp", type="primary", disabled=not confirmation, width="stretch"):
        progress = st.progress(0)
        config = st.session_state["selection_config"]
        results = execute_campaign(token, selection, DESTINATIONS[config["destination"]], config["tag"], selected_sellers, config["status"], progress)
        success_count = sum(1 for result in results if result["ok"])
        db_query(
            f"INSERT INTO {CAMPAIGN_RUNS_TABLE}(campaign,destination,audience_stage,selected_count,executed_count,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (config["tag"], config["destination"], ", ".join(config["stages"]), len(selection), success_count, username), fetch=None,
        )
        if success_count == len(selection):
            st.success(f"Campanha concluída: {success_count} cards atualizados, adicionados à lista e atribuídos aos vendedores.")
        else:
            st.warning(f"{success_count} de {len(selection)} cards concluídos. Verifique as permissões dos itens restantes.")
