from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from src.asksuite_api import AsksuiteClient, AsksuiteError
from src.asksuite_sync import index_status, rebuild_index, simulate, update_index_delta
from src.shared import (
    APP_SECRET_KEY, SESSIONS_TABLE, USERS_TABLE, db_query,
    get_secret, save_secret,
)


def current_user():
    cookies = EncryptedCookieManager(prefix="crm_intermares_", password=APP_SECRET_KEY)
    if not cookies.ready():
        st.stop()
    raw = cookies.get("session")
    if not raw:
        return None
    import hashlib
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return db_query(
        f"""SELECT u.id,u.username,u.role FROM {SESSIONS_TABLE} s
        JOIN {USERS_TABLE} u ON u.id=s.user_id
        WHERE s.token_hash=%s AND s.expires_at>NOW() AND u.active=TRUE""",
        (token_hash,), "one",
    )


user = current_user()
if not user:
    st.error("Faça login primeiro pela página principal do CRM Intermares.")
    st.stop()

_, username, role = user
st.set_page_config(page_title="Asksuite Sync · CRM Intermares", page_icon="IM", layout="wide")
st.title("Asksuite → ClickUp")
st.caption("Módulo BETA em modo seguro. Nenhum card é escrito por esta tela.")

if role != "admin":
    st.warning("A configuração e autenticação da Asksuite são restritas ao administrador.")

api_key = get_secret("asksuite_api_key")
email = get_secret("asksuite_email")
password = get_secret("asksuite_password")
access_token = get_secret("asksuite_access_token")

with st.expander("1. Conexão Asksuite", expanded=not bool(access_token)):
    st.write("Fluxo BETA: login → MFA por e-mail → /v1/auth/login/verify → accessToken.")
    if role == "admin":
        with st.form("asksuite_credentials"):
            new_email = st.text_input("E-mail Asksuite", value=email)
            new_api_key = st.text_input("API Key", type="password", placeholder="salva criptografada")
            new_password = st.text_input("Senha Asksuite", type="password", placeholder="salva criptografada")
            if st.form_submit_button("Salvar credenciais"):
                if new_email.strip():
                    save_secret("asksuite_email", new_email.strip(), username)
                if new_api_key.strip():
                    save_secret("asksuite_api_key", new_api_key.strip(), username)
                if new_password:
                    save_secret("asksuite_password", new_password, username)
                st.success("Credenciais salvas de forma criptografada.")
                st.rerun()

        api_key = get_secret("asksuite_api_key")
        email = get_secret("asksuite_email")
        password = get_secret("asksuite_password")

        if api_key and email and password:
            if st.button("Solicitar código MFA"):
                try:
                    result = AsksuiteClient(api_key=api_key).login(email, password)
                    st.session_state["asksuite_login_result"] = result
                    st.success("Login aceito. Verifique o código enviado por e-mail.")
                except Exception as exc:
                    st.error(str(exc))

            code = st.text_input("Código MFA", max_chars=12)
            if st.button("Confirmar MFA e salvar accessToken", disabled=not bool(code.strip())):
                try:
                    token, _ = AsksuiteClient(api_key=api_key).verify_mfa(email, password, code)
                    save_secret("asksuite_access_token", token, username)
                    save_secret("asksuite_token_verified_at", datetime.now(timezone.utc).isoformat(), username)
                    st.success("Asksuite autenticada.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    access_token = get_secret("asksuite_access_token")
    if access_token and api_key:
        if st.button("Testar conexão /v1/companies"):
            try:
                companies = AsksuiteClient(api_key=api_key, access_token=access_token).companies()
                st.success(f"Conexão válida. {len(companies)} empresa(s) retornada(s).")
                if companies:
                    st.dataframe(pd.DataFrame(companies).head(20), width="stretch")
            except Exception as exc:
                st.error(str(exc))

st.divider()
st.subheader("2. Índice seguro do ClickUp")
idx = index_status()
cols = st.columns(4)
cols[0].metric("Índice completo", "SIM" if idx["complete"] else "NÃO")
cols[1].metric("Chaves", idx["keys"])
cols[2].metric("Cards indexados", idx["tasks"])
cols[3].metric("Atualizado", idx["updated_at"] or "—")

clickup_token = get_secret("clickup_token")
if not clickup_token:
    st.error("Token do ClickUp ainda não foi configurado em Configurações.")
else:
    if idx["complete"]:
        if st.button("Atualizar índice (delta)"):
            try:
                result = update_index_delta(clickup_token)
                st.success(f"Delta concluído: {result['tasks_read']} cards lidos.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    else:
        st.warning("A primeira carga lê as três listas por mês. Pode demorar por causa dos +170 mil cards.")
        confirm = st.checkbox("Entendo que a carga inicial pode demorar e quero construir o índice agora")
        if st.button("Construir índice completo", disabled=not confirm):
            status_box = st.empty()
            try:
                result = rebuild_index(clickup_token, progress=lambda text: status_box.info(text))
                st.success(f"Índice concluído. {result['tasks_read']} cards lidos.")
                st.rerun()
            except Exception as exc:
                st.error(f"Indexação interrompida e marcada como incompleta: {exc}")

st.divider()
st.subheader("3. Ler Asksuite e simular")
if not access_token or not api_key:
    st.info("Autentique a Asksuite antes de consultar atendimentos.")
else:
    default_body = "{}"
    body_text = st.text_area(
        "Body do POST /v1/attendances",
        value=default_body,
        help="A API está em BETA. Mantemos o body editável até validar o formato exato da paginação/filtros liberados para esta conta.",
        height=110,
    )
    if st.button("Buscar atendimentos e SIMULAR", type="primary"):
        try:
            body = json.loads(body_text or "{}")
            client = AsksuiteClient(api_key=api_key, access_token=access_token)
            attendances, raw = client.attendances(body)
            st.session_state["asksuite_raw_response"] = raw
            st.session_state["asksuite_attendances"] = attendances
            st.success(f"{len(attendances)} atendimento(s) retornado(s).")
            details, stats = simulate(attendances)
            metrics = st.columns(8)
            labels = [
                ("Lidos", stats["attendances"]), ("Identificados", stats["identified"]),
                ("Encontrados", stats["matched"]), ("Novos", stats["new_cards"]),
                ("Comercial", stats["commercial"]), ("Pós-vendas", stats["post_sales"]),
                ("Descartados", stats["discarded"]), ("Erros", stats["errors"]),
            ]
            for col, (label, value) in zip(metrics, labels):
                col.metric(label, value)
            table = pd.DataFrame([
                {
                    "Atendimento": d.get("attendance_id"),
                    "Nome": d.get("name"),
                    "Telefone": d.get("phone"),
                    "E-mail": d.get("email"),
                    "Destino": d.get("destination"),
                    "Task ID": d.get("task_id"),
                    "Status atual": d.get("current_status"),
                    "Status alvo": d.get("target_status"),
                    "Ação": d.get("action"),
                }
                for d in details
            ])
            st.dataframe(table, width="stretch", hide_index=True)
        except json.JSONDecodeError:
            st.error("O body informado não é JSON válido.")
        except Exception as exc:
            st.error(str(exc))

if st.session_state.get("asksuite_raw_response"):
    with st.expander("Resposta bruta da API — diagnóstico BETA"):
        st.json(st.session_state["asksuite_raw_response"])

st.caption("Escrita real no ClickUp permanece bloqueada nesta versão até validarmos a resposta real da API e a taxa de correspondência.")
