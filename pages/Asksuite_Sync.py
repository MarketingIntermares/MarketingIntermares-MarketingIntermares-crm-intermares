from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager
from src.asksuite_api import AsksuiteClient, AsksuiteError, pick_access_token, pick_refresh_token
from src.asksuite_sync import index_status, rebuild_index, simulate, update_index_delta
from src.shared import APP_SECRET_KEY, SESSIONS_TABLE, USERS_TABLE, db_query, get_secret, save_secret

def current_user():
    cookies = EncryptedCookieManager(prefix="crm_intermares_", password=APP_SECRET_KEY)
    if not cookies.ready():
        st.stop()
    raw = cookies.get("session")
    if not raw:
        return None
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return db_query(
        f"""SELECT u.id,u.username,u.role FROM {SESSIONS_TABLE} s
        JOIN {USERS_TABLE} u ON u.id=s.user_id
        WHERE s.token_hash=%s AND s.expires_at>NOW() AND u.active=TRUE""",
        (token_hash,), "one"
    )

user = current_user()
if not user:
    st.error("Faça login primeiro pela página principal do CRM Intermares.")
    st.stop()

_, username, role = user
st.set_page_config(page_title="Asksuite Sync · CRM Intermares", page_icon="IM", layout="wide")
st.title("Asksuite → ClickUp")
st.caption("Módulo BETA em modo seguro. Nenhum cartão é escrito nesta tela.")

api_key = get_secret("asksuite_api_key")
email = get_secret("asksuite_email")
password = get_secret("asksuite_password")
access_token = get_secret("asksuite_access_token")

with st.expander("1. Conexão Asksuite", expanded=not bool(access_token)):
    st.write("Login público → accessToken direto ou MFA → chamadas protegidas com Bearer + X-API-Key.")
    if role == "admin":
        with st.form("asksuite_credentials"):
            new_email = st.text_input("E-mail Asksuite", value=email)
            new_api_key = st.text_input("API Key", type="password")
            new_password = st.text_input("Senha Asksuite", type="password")
            if st.form_submit_button("Salvar credenciais"):
                if new_email.strip(): save_secret("asksuite_email", new_email.strip(), username)
                if new_api_key.strip(): save_secret("asksuite_api_key", new_api_key.strip(), username)
                if new_password: save_secret("asksuite_password", new_password, username)
                st.success("Credenciais salvas de forma criptografada.")
                st.rerun()

        api_key = get_secret("asksuite_api_key")
        email = get_secret("asksuite_email")
        password = get_secret("asksuite_password")

        if email and password and st.button("Autenticar na Asksuite"):
            try:
                result = AsksuiteClient().login(email, password)
                token = pick_access_token(result)
                refresh = pick_refresh_token(result)
                if token:
                    save_secret("asksuite_access_token", token, username)
                    if refresh:
                        save_secret("asksuite_refresh_token", refresh, username)
                    save_secret("asksuite_token_verified_at", datetime.now(timezone.utc).isoformat(), username)
                    st.success("Login concluído e accessToken salvo.")
                    st.rerun()
                if result.get("mfaRequired") is True or result.get("mfa_required") is True:
                    st.session_state["asksuite_mfa_required"] = True
                    st.success("MFA solicitado. Verifique o código enviado por e-mail.")
                else:
                    st.error("Login respondeu sem accessToken e sem MFA reconhecido.")
                    st.json(result)
            except Exception as exc:
                st.error(str(exc))

        if st.session_state.get("asksuite_mfa_required"):
            code = st.text_input("Código MFA", max_chars=12)
            if st.button("Confirmar MFA e salvar accessToken", disabled=not bool(code.strip())):
                try:
                    result = AsksuiteClient().verify_mfa(email, password, code)
                    token = pick_access_token(result)
                    refresh = pick_refresh_token(result)
                    if not token:
                        raise AsksuiteError("O /v1/auth/login/verify respondeu sem accessToken reconhecível.")
                    save_secret("asksuite_access_token", token, username)
                    if refresh:
                        save_secret("asksuite_refresh_token", refresh, username)
                    save_secret("asksuite_token_verified_at", datetime.now(timezone.utc).isoformat(), username)
                    st.session_state.pop("asksuite_mfa_required", None)
                    st.success("MFA confirmado e accessToken salvo.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    access_token = get_secret("asksuite_access_token")
    if access_token and api_key and st.button("Testar conexão /v1/companies"):
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
c = st.columns(4)
c[0].metric("Índice completo", "SIM" if idx["complete"] else "NÃO")
c[1].metric("Chaves", idx["keys"])
c[2].metric("Cards indexados", idx["tasks"])
c[3].metric("Atualizado", idx["updated_at"] or "—")

clickup_token = get_secret("clickup_token")
if not clickup_token:
    st.error("Token do ClickUp ainda não foi configurado em Configurações.")
elif idx["complete"]:
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
        box = st.empty()
        try:
            result = rebuild_index(clickup_token, progress=lambda text: box.info(text))
            st.success(f"Índice concluído. {result['tasks_read']} cards lidos.")
            st.rerun()
        except Exception as exc:
            st.error(f"Indexação interrompida e marcada como incompleta: {exc}")

st.divider()
st.subheader("3. Ler Asksuite e simular")
if not access_token or not api_key:
    st.info("Autentique a Asksuite antes de consultar atendimentos.")
else:
    body_text = st.text_area("Body do POST /v1/attendances", value="{}", height=110)
    if st.button("Buscar atendimentos e SIMULAR", type="primary"):
        try:
            body = json.loads(body_text or "{}")
            attendances, raw = AsksuiteClient(api_key=api_key, access_token=access_token).attendances(body)
            st.session_state["asksuite_raw_response"] = raw
            st.success(f"{len(attendances)} atendimento(s) retornado(s).")
            details, stats = simulate(attendances)
            st.json(stats)
            table = pd.DataFrame([{
                "Atendimento": d.get("attendance_id"),
                "Nome": d.get("name"),
                "Telefone": d.get("phone"),
                "E-mail": d.get("email"),
                "Destino": d.get("destination"),
                "Task ID": d.get("task_id"),
                "Status atual": d.get("current_status"),
                "Status alvo": d.get("target_status"),
                "Ação": d.get("action"),
            } for d in details])
            st.dataframe(table, width="stretch", hide_index=True)
        except Exception as exc:
            st.error(str(exc))

if st.session_state.get("asksuite_raw_response"):
    with st.expander("Resposta bruta da API — diagnóstico BETA"):
        st.json(st.session_state["asksuite_raw_response"])
