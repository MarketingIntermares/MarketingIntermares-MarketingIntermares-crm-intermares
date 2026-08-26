from __future__ import annotations

import hashlib

import pandas as pd
import psycopg
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from src.conversas_asksuite import CONVERSAS_TABLE, detect_origin_conflict
from src.shared import APP_SECRET_KEY, DATABASE_URL, SESSIONS_TABLE, USERS_TABLE, db_query


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

st.set_page_config(page_title="Conversas Asksuite · CRM Intermares", page_icon="IM", layout="wide")
st.title("Conversas Asksuite — Nauticomar")
st.caption("Dados extraídos via scraping (não a API ao vivo) — agosto/2026, histórico expandido.")


@st.cache_data(ttl=300)
def load_df() -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as con:
        return pd.read_sql(f"SELECT * FROM {CONVERSAS_TABLE}", con)


df_all = load_df()

if df_all.empty:
    st.warning("Nenhum dado carregado ainda nessa tabela.")
    st.stop()

# data de referencia pra filtro: resolucao quando existe, senao 1a vez.
# a maioria dos "aberto" nao tem data_resolucao ainda.
df_all["data_referencia"] = pd.to_datetime(df_all["data_resolucao"]).fillna(
    pd.to_datetime(df_all["data_primeira_vez"])
)

data_min = df_all["data_referencia"].min()
data_max = df_all["data_referencia"].max()

with st.container(border=True):
    st.caption("Período (hoje só temos agosto/2026 — o filtro fica pronto pra quando tivermos mais meses)")
    intervalo = st.date_input(
        "Intervalo de datas",
        value=(data_min.date(), data_max.date()) if pd.notna(data_min) and pd.notna(data_max) else None,
        min_value=data_min.date() if pd.notna(data_min) else None,
        max_value=data_max.date() if pd.notna(data_max) else None,
        label_visibility="collapsed",
    )

if isinstance(intervalo, tuple) and len(intervalo) == 2:
    inicio, fim = intervalo
    mask = df_all["data_referencia"].dt.date.between(inicio, fim) | df_all["data_referencia"].isna()
    df = df_all[mask].copy()
else:
    df = df_all.copy()

st.caption(f"{len(df)} de {len(df_all)} conversas no período selecionado.")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total de conversas", len(df))
col2.metric("Vendedores identificados", df["vendedor"].replace("", pd.NA).nunique())
col3.metric("Com histórico expandido", int((df["qtd_atendimentos_historico"] > 1).sum()))
col4.metric("Sem origem identificável", int((df["origem_atual"] == "não identificado").sum()))

st.divider()

tab_vendedores, tab_origem, tab_etiquetas, tab_gaps, tab_dados = st.tabs(
    ["Vendedores", "Origem", "Etiquetas", "Gaps de identificação", "Dados brutos"]
)

with tab_vendedores:
    st.subheader("Desempenho por vendedor")
    st.caption("Ganhou = coluna do Kanban. Perdeu = etiqueta de texto no card (não a coluna — quase ninguém usa a coluna 'Perdeu').")

    vend = df[df["vendedor"] != ""].copy()
    resumo = vend.groupby("vendedor").agg(
        total=("card_key", "count"),
        ganhou=("esta_na_coluna_ganhou", "sum"),
        perdeu_tag=("tem_etiqueta_perdeu", "sum"),
    ).reset_index()
    resumo["com_desfecho"] = resumo["ganhou"] + resumo["perdeu_tag"]
    resumo["pct_desfecho"] = (resumo["com_desfecho"] / resumo["total"] * 100).round(1)
    resumo = resumo.sort_values("pct_desfecho", ascending=False)

    st.dataframe(
        resumo.rename(columns={
            "vendedor": "Vendedor", "total": "Total", "ganhou": "Ganhou",
            "perdeu_tag": "Perdeu (etiqueta)", "pct_desfecho": "% com desfecho explícito",
        })[["Vendedor", "Total", "Ganhou", "Perdeu (etiqueta)", "% com desfecho explícito"]],
        width="stretch", hide_index=True,
    )

    outlier = resumo.iloc[-1]
    if outlier["pct_desfecho"] < 30:
        st.warning(
            f"**{outlier['vendedor']}** está bem abaixo do resto do time "
            f"({outlier['pct_desfecho']}% de desfecho explícito vs. {resumo['pct_desfecho'].median():.0f}% mediano)."
        )

with tab_origem:
    st.subheader("Origem dos contatos")

    origem_counts = df["origem_atual"].value_counts().reset_index()
    origem_counts.columns = ["Origem", "Quantidade"]
    st.dataframe(origem_counts.head(15), width="stretch", hide_index=True)

    st.divider()
    st.subheader("Clientes que mudaram de canal (primeira vez → atual)")
    mudou = df[df["mudou_de_canal"] == True]  # noqa: E712
    st.metric("Mudaram de canal", f"{len(mudou)} de {int((df['qtd_atendimentos_historico'] > 1).sum())} com histórico real")
    if not mudou.empty:
        st.dataframe(
            mudou[["contato", "vendedor", "origem_primeira_vez", "data_primeira_vez", "origem_atual"]]
            .rename(columns={
                "contato": "Contato", "vendedor": "Vendedor",
                "origem_primeira_vez": "1ª vez", "data_primeira_vez": "Data 1ª vez",
                "origem_atual": "Atual",
            })
            .sort_values("Data 1ª vez")
            .head(50),
            width="stretch", hide_index=True,
        )

with tab_etiquetas:
    st.subheader("Etiquetas [ORI] e [CAM]")

    c1, c2 = st.columns(2)
    with c1:
        st.caption("Distribuição [ORI] (origem)")
        ori_counts = df[df["tag_ori_original"] != ""]["tag_ori_original"].value_counts().reset_index()
        ori_counts.columns = ["Tag [ORI]", "Quantidade"]
        st.dataframe(ori_counts.head(15), width="stretch", hide_index=True)
    with c2:
        st.caption("Distribuição [CAM] (campanha)")
        cam_counts = df[df["tag_cam_original"] != ""]["tag_cam_original"].value_counts().reset_index()
        cam_counts.columns = ["Tag [CAM]", "Quantidade"]
        st.dataframe(cam_counts.head(15), width="stretch", hide_index=True)

    st.divider()
    st.subheader("Conflitos: tag do card vs. o que o cliente diz no texto")
    st.caption(
        "Só sinaliza quando o cliente menciona a origem explicitamente nos primeiros ~700 caracteres "
        "da conversa. A maioria dos casos não tem essa pista — não significa que a tag está errada, "
        "só que não dá pra confirmar nem contestar pelo texto."
    )

    if st.button("Rodar detecção de conflitos (pode levar alguns segundos)"):
        conflitos = []
        for _, row in df.iterrows():
            resultado = detect_origin_conflict(row["conversation_text"], row["tag_ori_original"])
            if resultado:
                conflitos.append({
                    "Contato": row["contato"],
                    "Vendedor": row["vendedor"],
                    "Tag atual": row["tag_ori_original"] or "(ausente)",
                    "Texto sugere": resultado["categoria_sugerida"],
                    "Status": "Tag pode estar errada" if resultado["status"] == "tag_diverge" else "Sem tag, texto sugere uma",
                    "Evidência": resultado["evidencia"],
                })
        st.session_state["conflitos_tag"] = conflitos

    conflitos = st.session_state.get("conflitos_tag")
    if conflitos is not None:
        st.metric("Conflitos encontrados", len(conflitos))
        if conflitos:
            st.dataframe(pd.DataFrame(conflitos), width="stretch", hide_index=True)

with tab_gaps:
    st.subheader("Conversas sem vendedor nem origem identificados")
    sem_id = df[(df["vendedor"] == "") & (df["origem_atual"] == "não identificado")]
    st.metric("Total sem identificação", len(sem_id))

    if not sem_id.empty:
        # canal aparece dentro do card_raw_text as vezes -- aqui so mostramos coluna/status
        por_coluna = sem_id["coluna"].value_counts().reset_index()
        por_coluna.columns = ["Coluna", "Quantidade"]
        st.dataframe(por_coluna, width="stretch", hide_index=True)

with tab_dados:
    st.subheader("Explorar dados brutos")
    vendedores_opts = ["(todos)"] + sorted(v for v in df["vendedor"].unique() if v)
    filtro_vendedor = st.selectbox("Filtrar por vendedor", vendedores_opts)
    filtro_texto = st.text_input("Buscar por nome de contato")

    filtrado = df.copy()
    if filtro_vendedor != "(todos)":
        filtrado = filtrado[filtrado["vendedor"] == filtro_vendedor]
    if filtro_texto.strip():
        filtrado = filtrado[filtrado["contato"].str.contains(filtro_texto.strip(), case=False, na=False)]

    st.dataframe(
        filtrado[["contato", "vendedor", "coluna", "origem_atual", "origem_primeira_vez", "data_resolucao"]],
        width="stretch", hide_index=True,
    )
    st.caption(f"{len(filtrado)} de {len(df)} registros.")
