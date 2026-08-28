from __future__ import annotations

import hashlib

import pandas as pd
import psycopg
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from src.conversas_asksuite import (
    CONVERSAS_TABLE,
    detect_origin_conflict,
    detect_rejection_moment,
    extract_timeline_events,
)
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

st.set_page_config(page_title="Conversas Asksuite Intermares · CRM Intermares", page_icon="IM", layout="wide")
st.title("Conversas Asksuite — Intermares (Rede)")
st.caption(
    "Dados extraídos via scraping (não a API ao vivo) — agosto/2026, histórico expandido. "
    "Pipeline diferente do board Nauticomar: aqui são etapas de contato/follow-up, não negociação de venda."
)

# ordem visual do funil deste board -- puramente pra exibicao (o board nao
# guarda uma "ordem" explicita, so o nome de cada coluna). Nomes exatamente
# como aparecem no Kanban (casing inconsistente entre colunas e assim mesmo
# no proprio Asksuite, nao e erro de extracao).
ORDEM_FUNIL = [
    "Atendimentos Iniciados", "EM ATENDIMENTO", "FOLLOW-UP AGENDADO",
    "CALL AGENDADA", "ATENDIMENTO ENCERRADO", "MENÇÕES DO INSTAGRAM",
]


@st.cache_data(ttl=300)
def load_df() -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as con:
        return pd.read_sql(f"SELECT * FROM {CONVERSAS_TABLE} WHERE board = 'intermares'", con)


df_all = load_df()

if df_all.empty:
    st.warning("Nenhum dado do Intermares carregado ainda nessa tabela.")
    st.stop()

df_all["data_referencia"] = pd.to_datetime(df_all["data_resolucao"]).fillna(
    pd.to_datetime(df_all["data_primeira_vez"])
)

data_min = df_all["data_referencia"].min()
data_max = df_all["data_referencia"].max()

with st.container(border=True):
    st.caption("Filtros (afetam todas as abas)")
    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
    with f1:
        intervalo = st.date_input(
            "Intervalo de datas",
            value=(data_min.date(), data_max.date()) if pd.notna(data_min) and pd.notna(data_max) else None,
            min_value=data_min.date() if pd.notna(data_min) else None,
            max_value=data_max.date() if pd.notna(data_max) else None,
        )
    with f2:
        vendedor_opts = ["(todos)"] + sorted(v for v in df_all["vendedor"].unique() if v)
        filtro_vendedor_top = st.selectbox("Vendedor", vendedor_opts)
    with f3:
        coluna_opts = ["(todas)"] + sorted(df_all["coluna"].unique())
        filtro_coluna_top = st.selectbox("Etapa", coluna_opts)
    with f4:
        origem_opts = ["(todas)"] + sorted(df_all["origem_atual"].unique())
        filtro_origem_top = st.selectbox("Origem", origem_opts)

df = df_all.copy()

if isinstance(intervalo, tuple) and len(intervalo) == 2:
    inicio, fim = intervalo
    mask = df["data_referencia"].dt.date.between(inicio, fim) | df["data_referencia"].isna()
    df = df[mask]

if filtro_vendedor_top != "(todos)":
    df = df[df["vendedor"] == filtro_vendedor_top]
if filtro_coluna_top != "(todas)":
    df = df[df["coluna"] == filtro_coluna_top]
if filtro_origem_top != "(todas)":
    df = df[df["origem_atual"] == filtro_origem_top]

df = df.copy()
st.caption(f"{len(df)} de {len(df_all)} conversas com os filtros atuais.")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total de conversas", len(df))
col2.metric("Vendedores identificados", df["vendedor"].replace("", pd.NA).nunique())
col3.metric("Converteram (SOU MEMBRO)", int(df["converteu"].sum()))
col4.metric("Sem origem identificável", int((df["origem_atual"] == "não identificado").sum()))

st.divider()

tab_funil, tab_vendedores, tab_origem, tab_etiquetas, tab_conversao, tab_gaps, tab_dados = st.tabs(
    ["Funil", "Vendedores", "Origem", "Etiquetas", "Conversão", "Gaps de identificação", "Dados brutos"]
)

with tab_funil:
    st.subheader("Onde as conversas estão paradas")
    st.caption(
        "O Intermares não tem colunas de negociação (cotação, ganhou, perdeu) como a Nauticomar — "
        "é um funil de contato: iniciado → em atendimento → follow-up/call agendados → encerrado. "
        "Menções do Instagram é uma esteira à parte (não é etapa de venda)."
    )

    contagem = df["coluna"].value_counts()
    ordem_presente = [c for c in ORDEM_FUNIL if c in contagem.index] + [
        c for c in contagem.index if c not in ORDEM_FUNIL
    ]
    contagem = contagem.reindex(ordem_presente)
    st.bar_chart(contagem)

    st.divider()
    st.subheader("Funil por status (aberto vs. resolvido)")
    pivot_status = pd.crosstab(df["coluna"], df["status_filtro"]).reindex(ordem_presente)
    st.dataframe(pivot_status, width="stretch")

with tab_vendedores:
    st.subheader("Desempenho por vendedor")
    st.caption(
        "Convertido = etiqueta 'SOU MEMBRO' no card — o Intermares não tem coluna de ganhou/perdeu "
        "como a Nauticomar, então o desfecho é rastreado por etiqueta, não por posição no Kanban."
    )

    vend = df[df["vendedor"] != ""].copy()
    if vend.empty:
        st.info("Nenhuma conversa com vendedor identificado no período selecionado.")
    else:
        resumo = vend.groupby("vendedor").agg(
            total=("card_key", "count"),
            convertido=("converteu", "sum"),
        ).reset_index()
        resumo["pct_conversao"] = (resumo["convertido"] / resumo["total"] * 100).round(1)
        resumo = resumo.sort_values("pct_conversao", ascending=False)

        st.dataframe(
            resumo.rename(columns={
                "vendedor": "Vendedor", "total": "Total", "convertido": "Converteu (SOU MEMBRO)",
                "pct_conversao": "% conversão",
            }),
            width="stretch", hide_index=True,
        )

        st.divider()
        st.subheader("Rejeição explícita do cliente — o vendedor insistiu depois?")
        st.caption(
            "Detecção por palavra-chave (não IA) de frases como 'não, obrigada', 'não tenho interesse', etc. "
            "Pode ter falso positivo/negativo — sempre confira a evidência antes de usar pra avaliar alguém."
        )
        if st.button("Rodar detecção de rejeição", key="rejeicao_intermares"):
            achados = []
            for _, row in vend.iterrows():
                r = detect_rejection_moment(row["conversation_text"])
                if r:
                    achados.append({"vendedor": row["vendedor"], **r})
            st.session_state["rejeicoes_intermares"] = achados

        rejeicoes = st.session_state.get("rejeicoes_intermares")
        if rejeicoes is not None:
            if not rejeicoes:
                st.info("Nenhuma frase de rejeição detectada no período selecionado.")
            else:
                rdf = pd.DataFrame(rejeicoes)
                resumo_rej = rdf.groupby("vendedor").agg(
                    total_rejeicoes=("vendedor", "count"),
                    sem_resposta_depois=("vendedor_respondeu_depois", lambda s: (~s).sum()),
                ).reset_index().sort_values("sem_resposta_depois", ascending=False)
                st.dataframe(
                    resumo_rej.rename(columns={
                        "vendedor": "Vendedor", "total_rejeicoes": "Rejeições detectadas",
                        "sem_resposta_depois": "Conversa parou logo depois",
                    }),
                    width="stretch", hide_index=True,
                )
                with st.expander("Ver evidências individuais"):
                    st.dataframe(
                        rdf.rename(columns={
                            "vendedor": "Vendedor", "frase_rejeicao": "Frase da rejeição",
                            "depois_da_rejeicao": "O que veio depois", "vendedor_respondeu_depois": "Teve algo depois",
                        }),
                        width="stretch", hide_index=True,
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

    if st.button("Rodar detecção de conflitos (pode levar alguns segundos)", key="conflitos_intermares"):
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
        st.session_state["conflitos_tag_intermares"] = conflitos

    conflitos = st.session_state.get("conflitos_tag_intermares")
    if conflitos is not None:
        st.metric("Conflitos encontrados", len(conflitos))
        if conflitos:
            st.dataframe(pd.DataFrame(conflitos), width="stretch", hide_index=True)

with tab_conversao:
    st.subheader("Tempo até conversão (primeiro contato → virou membro)")
    st.caption(
        "Usa a data do primeiro contato de verdade (considerando histórico expandido, "
        "mesmo que tenha sido anos atrás) até a data em que o atendimento com a etiqueta "
        "'SOU MEMBRO' foi marcado como resolvido."
    )

    convertidos = df[
        (df["converteu"]) &
        (df["data_primeira_vez"].notna()) &
        (df["data_resolucao"].notna())
    ].copy()

    if convertidos.empty:
        st.info("Nenhuma conversão com as duas datas preenchidas no período selecionado.")
    else:
        convertidos["dias_ate_conversao"] = (
            pd.to_datetime(convertidos["data_resolucao"]) - pd.to_datetime(convertidos["data_primeira_vez"])
        ).dt.days

        c1, c2, c3 = st.columns(3)
        c1.metric("Conversões analisadas", len(convertidos))
        c2.metric("Média (dias)", f"{convertidos['dias_ate_conversao'].mean():.0f}")
        c3.metric("Mediana (dias)", f"{convertidos['dias_ate_conversao'].median():.0f}")

        faixas = [0, 1, 30, 60, 120, 365, 100000]
        rotulos = ["Mesmo dia", "1–30 dias", "31–60 dias", "61–120 dias", "121–365 dias", "Mais de 1 ano"]
        convertidos["faixa"] = pd.cut(convertidos["dias_ate_conversao"], bins=faixas, labels=rotulos, right=True, include_lowest=True)

        faixa_counts = convertidos["faixa"].value_counts().reindex(rotulos).reset_index()
        faixa_counts.columns = ["Faixa", "Quantidade"]
        st.bar_chart(faixa_counts.set_index("Faixa"))
        st.dataframe(faixa_counts, width="stretch", hide_index=True)

        st.divider()
        st.subheader("Conversões mais longas (clientes reativados depois de muito tempo)")
        st.dataframe(
            convertidos.sort_values("dias_ate_conversao", ascending=False)
            [["contato", "vendedor", "origem_primeira_vez", "data_primeira_vez", "data_resolucao", "dias_ate_conversao"]]
            .rename(columns={
                "contato": "Contato", "vendedor": "Vendedor", "origem_primeira_vez": "Origem 1ª vez",
                "data_primeira_vez": "1º contato", "data_resolucao": "Converteu em", "dias_ate_conversao": "Dias",
            })
            .head(20),
            width="stretch", hide_index=True,
        )

with tab_gaps:
    st.subheader("Conversas sem vendedor nem origem identificados")
    sem_id = df[(df["vendedor"] == "") & (df["origem_atual"] == "não identificado")].copy()
    st.metric("Total sem identificação", len(sem_id))

    if not sem_id.empty:
        por_coluna = sem_id["coluna"].value_counts().reset_index()
        por_coluna.columns = ["Coluna", "Quantidade"]
        st.dataframe(por_coluna, width="stretch", hide_index=True)

        st.divider()
        st.caption("Lista completa (clique numa coluna do cabeçalho pra ordenar).")
        filtro_coluna_gap = st.selectbox(
            "Filtrar por coluna", ["(todas)"] + sorted(sem_id["coluna"].unique()), key="gap_coluna_intermares"
        )
        gap_filtrado = sem_id if filtro_coluna_gap == "(todas)" else sem_id[sem_id["coluna"] == filtro_coluna_gap]
        st.dataframe(
            gap_filtrado[["contato", "coluna", "status_filtro", "data_primeira_vez"]]
            .rename(columns={
                "contato": "Contato", "coluna": "Coluna",
                "status_filtro": "Status", "data_primeira_vez": "Data",
            })
            .sort_values("Data", na_position="last"),
            width="stretch", hide_index=True,
        )

with tab_dados:
    st.subheader("Explorar dados brutos")
    st.caption("Já respeita os filtros de vendedor/etapa/origem lá em cima — aqui só busca por nome.")
    filtro_texto = st.text_input("Buscar por nome de contato", key="busca_intermares")

    filtrado = df.copy()
    if filtro_texto.strip():
        filtrado = filtrado[filtrado["contato"].str.contains(filtro_texto.strip(), case=False, na=False)]

    filtrado = filtrado.reset_index(drop=True)
    st.caption(f"{len(filtrado)} de {len(df)} registros. Clique numa linha pra ver a linha do tempo do cliente.")

    selecao = st.dataframe(
        filtrado[[
            "contato", "vendedor", "coluna", "origem_atual",
            "data_primeira_vez", "data_resolucao",
        ]].rename(columns={
            "data_primeira_vez": "1ª interação",
            "data_resolucao": "Última interação (se resolvido)",
        }),
        width="stretch", hide_index=True,
        on_select="rerun", selection_mode="single-row", key="tabela_intermares",
    )

    linhas_selecionadas = selecao.selection.rows if selecao and selecao.selection else []
    if linhas_selecionadas:
        cliente = filtrado.iloc[linhas_selecionadas[0]]
        st.divider()
        st.subheader(f"Linha do tempo — {cliente['contato']}")

        eventos = extract_timeline_events(cliente["conversation_text"], cliente["card_raw_text"])
        if not eventos:
            st.info("Nenhum marco estruturado encontrado nessa conversa.")
        else:
            c1, c2 = st.columns(2)
            c1.metric("Primeira interação", f"{eventos[0]['data']} {eventos[0]['hora']}")
            c2.metric("Última interação", f"{eventos[-1]['data']} {eventos[-1]['hora']}")

            for ev in eventos:
                st.markdown(f"**{ev['data']} {ev['hora']}** — {ev['tipo']}" + (f": {ev['detalhe']}" if ev["detalhe"] else ""))

        rejeicao = detect_rejection_moment(cliente["conversation_text"])
        if rejeicao:
            st.divider()
            st.warning("Rejeição explícita detectada nessa conversa (revise antes de tirar conclusão):")
            st.markdown(f"**Trecho:** ...{rejeicao['frase_rejeicao']}...")
            st.markdown(f"**Depois:** {rejeicao['depois_da_rejeicao']}")
