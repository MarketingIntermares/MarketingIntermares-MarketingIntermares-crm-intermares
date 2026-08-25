from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from src.asksuite import assign_vendors, to_asksuite_csv
from src.auth import authenticate, get_cookie_manager, logout, persist_login, restore_login
from src.config import settings
from src.db import initialize_db
from src.segmentation import segment


st.set_page_config(
    page_title="CRM Intermares",
    page_icon="🌊",
    layout="wide",
)

initialize_db()

cookies = get_cookie_manager()
if not cookies.ready():
    st.stop()

if "authenticated_user" not in st.session_state:
    st.session_state.authenticated_user = restore_login(cookies)

if not st.session_state.authenticated_user:
    st.title("CRM Intermares")
    st.caption("Acesso restrito")

    with st.form("login_form"):
        username = st.text_input("Usuário")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar", width="stretch")

    if submitted:
        if authenticate(username, password):
            persist_login(cookies, username)
            st.session_state.authenticated_user = username
            st.rerun()
        else:
            st.error("Usuário ou senha inválidos.")
    st.stop()

with st.sidebar:
    st.title("CRM Intermares")
    st.caption(f"Release: {settings.app_release}")
    page = st.radio(
        "Navegação",
        ["Dashboard", "Segmentação", "Integrações", "Sistema"],
    )
    st.divider()
    st.write(f"Usuário: **{st.session_state.authenticated_user}**")
    if st.button("Sair", width="stretch"):
        logout(cookies)
        st.session_state.authenticated_user = None
        st.rerun()

if page == "Dashboard":
    st.title("Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Base de Leads 1", "—")
    c2.metric("Base de Leads 2", "—")
    c3.metric("Programa de Membros", "—")
    c4.metric("Público elegível", "—")

    st.info(
        "Reconstrução V1 ativa. O Railway de produção não foi alterado. "
        "Esta base serve para homologação do novo código versionado."
    )

elif page == "Segmentação":
    st.title("Segmentação de público")
    st.caption(
        "O público final vem apenas das duas Bases de Leads. "
        "Programa de Membros é usado somente para conferência/exclusão. "
        "PMS preenchido é tratado como conversão."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        f1 = st.file_uploader("Base de Leads 1", type=["csv", "xlsx"], key="base1")
    with col2:
        f2 = st.file_uploader("Base de Leads 2", type=["csv", "xlsx"], key="base2")
    with col3:
        fm = st.file_uploader("Programa de Membros", type=["csv", "xlsx"], key="members")

    def read_upload(uploaded):
        if uploaded is None:
            return None
        name = uploaded.name.lower()
        if name.endswith(".csv"):
            return pd.read_csv(uploaded, dtype=str)
        return pd.read_excel(uploaded, dtype=str)

    if f1 and f2:
        base1 = read_upload(f1)
        base2 = read_upload(f2)
        members = read_upload(fm) if fm else None

        result = segment(base1, base2, members)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Público final", len(result.audience))
        m2.metric("Conversões/PMS excluídas", result.excluded_converted)
        m3.metric("Membros excluídos", result.excluded_members)
        m4.metric("Duplicados removidos", result.duplicates_removed)

        campaign = st.text_input("Nome da campanha", value="[CAM] NOVA CAMPANHA")
        split_vendors = st.checkbox("Alternar 50/50 entre vendedores", value=True)

        audience = result.audience
        if split_vendors:
            audience = assign_vendors(
                audience,
                settings.asksuite_vendor_a,
                settings.asksuite_vendor_b,
            )

        st.dataframe(audience, width="stretch", hide_index=True)

        csv_bytes = to_asksuite_csv(audience, campaign)
        st.download_button(
            "Baixar CSV padrão Asksuite",
            data=csv_bytes,
            file_name="asksuite_segmentacao.csv",
            mime="text/csv",
            width="stretch",
        )
    else:
        st.warning("Envie pelo menos Base de Leads 1 e Base de Leads 2.")

elif page == "Integrações":
    st.title("Integrações")
    st.subheader("ClickUp")
    if settings.clickup_token:
        st.success("CLICKUP_TOKEN configurado.")
    else:
        st.warning("CLICKUP_TOKEN ainda não configurado neste ambiente.")

    st.subheader("Asksuite")
    st.write(
        "A V1 já gera CSV padronizado e alternância 50/50. "
        "API/webhook entram na próxima etapa de homologação."
    )

    st.subheader("Banco")
    st.write("Postgres" if settings.database_url else "SQLite local (fallback de desenvolvimento)")

elif page == "Sistema":
    st.title("Sistema")
    st.json(
        {
            "release": settings.app_release,
            "database": "postgres" if settings.database_url else "sqlite",
            "clickup_configured": bool(settings.clickup_token),
            "sync_dry_run": settings.sync_dry_run,
        }
    )
    st.success("Healthcheck do Streamlit: /_stcore/health")
