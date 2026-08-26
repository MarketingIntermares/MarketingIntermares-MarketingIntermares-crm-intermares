"""
Modulo de conversas extraidas do Asksuite (Nauticomar) para analise --
diferente de asksuite.py, que prepara CSV de campanha pra ENVIAR pro
Asksuite. Aqui e o sentido contrario: dado ja extraido DO Asksuite,
carregado nesse banco pra virar dashboard.

Segue o mesmo padrao de shared.py: tabela prefixada por DB_NAMESPACE,
psycopg puro, sem ORM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .shared import DB_NAMESPACE, db_query

CONVERSAS_TABLE = f"{DB_NAMESPACE}_asksuite_conversas"

ATT_RE = re.compile(r"ATT-\s*([A-ZÀ-Ú][A-ZÀ-Úa-zà-ú]+)")
ORI_RE = re.compile(r"\[ORI\]\s*([^\[\]]+?)(?=\s{2,}|\[|$)")
CAM_RE = re.compile(r"\[CAMP?\]\s*([^\[\]]+?)(?=\s{2,}|\[|$)")
PERDEU_TAG_RE = re.compile(r"Perdeu-?\s*([^\[\]]*?)(?=\s{2,}|\[|$)")
INICIADO_RE = re.compile(r"Atendimento iniciado pel[oa] ([^\n]+?) em (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})")
ANUNCIO_RE = re.compile(r"Anúncio:\s*([^\n]+)\nID:\s*(\d+)")
RESOLVIDO_RE = re.compile(r"marcou o atendimento como resolvido em (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})")
ATRIBUIDO_RE = re.compile(r"[Aa]tendimento atribuíd[oa] (?:automaticamente )?para ([^\n]+?) em (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})")
ROBO_PARADO_RE = re.compile(r"O robô foi parado por ([^\n]+?) em (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})")
RESOLVIDO_QUEM_RE = re.compile(r"([^\n]+?) marcou o atendimento como resolvido em (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})")
ANTERIOR_COLAPSADO_RE = re.compile(r"Carregar o atendimento anterior realizado em (\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})")


def extract_timeline_events(conversation_text: str, card_raw_text: str = "") -> list[dict]:
    """
    Extrai os marcos estruturados da conversa (nao o texto livre das
    mensagens) em ordem cronologica -- pra montar a linha do tempo por
    cliente. So usa marcadores que o proprio Asksuite gera (data/hora
    reais), nao tenta resumir o que foi dito.
    """
    text = conversation_text or ""
    eventos = []

    for m in INICIADO_RE.finditer(text):
        eventos.append({"data": m.group(2), "hora": m.group(3), "tipo": "Atendimento iniciado", "detalhe": m.group(1).strip()})

    for m in ATRIBUIDO_RE.finditer(text):
        eventos.append({"data": m.group(2), "hora": m.group(3), "tipo": "Atribuído a vendedor", "detalhe": m.group(1).strip()})

    for m in ROBO_PARADO_RE.finditer(text):
        eventos.append({"data": m.group(2), "hora": m.group(3), "tipo": "Vendedor assumiu (robô parado)", "detalhe": m.group(1).strip()})

    for m in RESOLVIDO_QUEM_RE.finditer(text):
        quem = m.group(1).strip().split("\n")[-1]  # pega so a ultima linha antes do "marcou..."
        eventos.append({"data": m.group(2), "hora": m.group(3), "tipo": "Marcado como resolvido", "detalhe": quem})

    for m in ANTERIOR_COLAPSADO_RE.finditer(text):
        eventos.append({"data": m.group(1), "hora": m.group(2), "tipo": "Atendimento anterior (expandido)", "detalhe": ""})

    m_anuncio = ANUNCIO_RE.search(text)
    if m_anuncio and eventos:
        # associa o anuncio ao primeiro "Atendimento iniciado" (mesmo atendimento)
        primeiro_inicio = min(
            (e for e in eventos if e["tipo"] == "Atendimento iniciado"),
            key=lambda e: (e["data"], e["hora"]), default=None,
        )
        if primeiro_inicio:
            eventos.append({
                "data": primeiro_inicio["data"], "hora": primeiro_inicio["hora"],
                "tipo": "Origem: anúncio", "detalhe": m_anuncio.group(1).strip(),
            })

    m_perdeu = PERDEU_TAG_RE.search(card_raw_text or "")
    if m_perdeu and eventos:
        ultimo_resolvido = max(
            (e for e in eventos if e["tipo"] == "Marcado como resolvido"),
            key=lambda e: (e["data"], e["hora"]), default=None,
        )
        if ultimo_resolvido:
            eventos.append({
                "data": ultimo_resolvido["data"], "hora": ultimo_resolvido["hora"],
                "tipo": "Motivo da perda", "detalhe": m_perdeu.group(1).strip() or "(sem motivo especificado)",
            })

    def _key(e):
        dd, mm, yy = e["data"].split("/")
        return (yy, mm, dd, e["hora"])

    eventos.sort(key=_key)
    return eventos

# frases em 1a pessoa que o CLIENTE usa pra dizer de onde veio -- mais preciso
# que so procurar a palavra solta (que tambem aparece no pitch do vendedor,
# tipo "segue a gente no instagram"). Mesma logica ja validada no CSV
# mapeamento_origem_agosto_final.csv.
ORIGIN_PHRASES = [
    ("instagram", re.compile(r"\b(vi|achei|encontrei|vim|caiu|apareceu).{0,15}\binstagram\b", re.IGNORECASE)),
    ("instagram", re.compile(r"\binstagram.{0,20}\b(anúncio|an[uú]ncio|propaganda)\b", re.IGNORECASE)),
    ("facebook", re.compile(r"\b(vi|achei|encontrei|vim|caiu|apareceu).{0,15}\bface(book)?\b", re.IGNORECASE)),
    ("google", re.compile(r"\b(vi|achei|encontrei|vim|pesquisei).{0,15}\bgoogle\b", re.IGNORECASE)),
    ("anuncio_generico", re.compile(r"\b(vi|clique[i]?|cliquei).{0,10}\b(no |o )?an[uú]ncio\b", re.IGNORECASE)),
    ("indicacao", re.compile(r"\bme indicou\b|\bnos indicou\b|\bamig[ao] (minha |meu )?indicou\b", re.IGNORECASE)),
    ("indicacao", re.compile(r"indica(ç|c)[aã]o de [A-ZÀ-Ú][a-zà-ú]+")),
]

COMPATIBLE_TAG_SUBSTR = {
    "instagram": ["instagram", "meta"],
    "facebook": ["facebook", "meta"],
    "google": ["google"],
    "anuncio_generico": ["meta", "google", "ads"],
    "indicacao": ["indica"],
}

SEARCH_WINDOW = 700


def detect_origin_conflict(conversation_text: str, tag_ori: str) -> dict | None:
    """
    Compara a tag [ORI] do card com o que o cliente diz nos primeiros ~700
    chars da conversa. Devolve None se nao ha pista textual (maioria dos
    casos) ou {'status', 'categoria_sugerida', 'evidencia'} se ha.
    """
    window = (conversation_text or "")[:SEARCH_WINDOW]
    for categoria, rx in ORIGIN_PHRASES:
        m = rx.search(window)
        if not m:
            continue
        start = max(0, m.start() - 40)
        end = min(len(window), m.end() + 40)
        evidencia = window[start:end].replace("\n", " ").strip()

        compat = COMPATIBLE_TAG_SUBSTR.get(categoria, [])
        tag_lower = (tag_ori or "").lower()
        compativel = any(s in tag_lower for s in compat)

        if tag_ori and compativel:
            return None  # tag bate com o texto, sem conflito
        status = "tag_diverge" if tag_ori else "sem_tag_com_sinal"
        return {"status": status, "categoria_sugerida": categoria, "evidencia": evidencia}
    return None


def _to_iso_date(d: str) -> str | None:
    if not d:
        return None
    dd, mm, yy = d.split("/")
    return f"{yy}-{mm}-{dd}"


def _extrair_nome_contato(card_raw_text: str) -> str:
    texto = re.sub(r"^(chat_bubble(_outline)?|check_circle_outline)\s*", "", card_raw_text)
    texto = re.sub(
        r"^(ABERTO|RESOLVIDO)?\s*(schedule(\d+[hms]\s*)+)?\s*(\d{1,2}(:\d{2}|/\d{2}/\d{2}))?\s*",
        "", texto,
    )
    texto = re.sub(r"^\d+\s+", "", texto)
    m = re.search(r"(Nauticomar Resort|Nauticomar\b)", texto)
    nome = texto[: m.start()].strip() if m else texto[:40].strip()
    return nome or "(sem nome)"


def _extrair_atendimentos(text: str):
    marcadores = list(INICIADO_RE.finditer(text))
    resultado = []
    for i, m in enumerate(marcadores):
        fim_janela = marcadores[i + 1].start() if i + 1 < len(marcadores) else len(text)
        janela = text[m.end():fim_janela]
        m_anuncio = ANUNCIO_RE.search(janela)
        resultado.append({
            "pos": m.start(),
            "canal": m.group(1).strip(),
            "data": m.group(2),
            "hora": m.group(3),
            "anuncio": m_anuncio.group(1).strip() if m_anuncio else None,
        })
    return resultado


@dataclass
class ConversaParsed:
    card_key: str
    board: str
    status_filtro: str
    coluna: str
    contato: str
    vendedor: str
    tag_ori_original: str
    tag_cam_original: str
    origem_atual: str
    origem_primeira_vez: str
    data_primeira_vez: str | None
    mudou_de_canal: bool
    qtd_atendimentos_historico: int
    tem_etiqueta_perdeu: bool
    motivo_perda: str
    esta_na_coluna_ganhou: bool
    data_resolucao: str | None
    tamanho_conversa_chars: int
    atendimentos_anteriores_expandidos: int
    card_raw_text: str
    conversation_text: str
    extraido_em: str | None


def parse_record(r: dict) -> ConversaParsed:
    card = r.get("card_raw_text", "") or ""
    text = r.get("conversation_text", "") or ""

    m_att = ATT_RE.search(card)
    vendedor = m_att.group(1).strip().upper() if m_att else ""

    m_ori = ORI_RE.search(card)
    tag_ori = m_ori.group(1).strip() if m_ori else ""
    m_cam = CAM_RE.search(card)
    tag_cam = m_cam.group(1).strip() if m_cam else ""

    m_perdeu = PERDEU_TAG_RE.search(card)
    tem_perdeu = bool(m_perdeu)
    motivo_perda = m_perdeu.group(1).strip() if m_perdeu else ""

    atendimentos = _extrair_atendimentos(text)
    atendimentos.sort(key=lambda a: a["pos"])

    if atendimentos:
        atual = atendimentos[0]
        primeiro = min(atendimentos, key=lambda a: _to_iso_date(a["data"]))
        canal_atual = f"Anúncio: {atual['anuncio']}" if atual["anuncio"] else atual["canal"]
        canal_primeiro = f"Anúncio: {primeiro['anuncio']}" if primeiro["anuncio"] else primeiro["canal"]
        origem_atual = tag_ori or canal_atual
        origem_primeira = canal_primeiro
        data_primeira = _to_iso_date(primeiro["data"])
        tem_historico_real = len(atendimentos) > 1
        mudou = tem_historico_real and canal_atual.strip().lower() != canal_primeiro.strip().lower()
    else:
        origem_atual = tag_ori or "não identificado"
        origem_primeira = "não identificado"
        data_primeira = None
        mudou = False

    m_resolvido = RESOLVIDO_RE.findall(text)  # 2 grupos (data, hora) -> findall devolve tuplas
    data_resolucao = _to_iso_date(m_resolvido[0][0]) if m_resolvido else None

    return ConversaParsed(
        card_key=r.get("_card_key", ""),
        board=r.get("board", ""),
        status_filtro=r.get("status_filtro", ""),
        coluna=r.get("coluna", ""),
        contato=_extrair_nome_contato(card),
        vendedor=vendedor,
        tag_ori_original=tag_ori,
        tag_cam_original=tag_cam,
        origem_atual=origem_atual,
        origem_primeira_vez=origem_primeira,
        data_primeira_vez=data_primeira,
        mudou_de_canal=mudou,
        qtd_atendimentos_historico=len(atendimentos),
        tem_etiqueta_perdeu=tem_perdeu,
        motivo_perda=motivo_perda,
        esta_na_coluna_ganhou=(r.get("coluna") == "ganhou"),
        data_resolucao=data_resolucao,
        tamanho_conversa_chars=len(text),
        atendimentos_anteriores_expandidos=r.get("atendimentos_anteriores_expandidos", 0) or 0,
        card_raw_text=card,
        conversation_text=text,
        extraido_em=r.get("extraido_em"),
    )


def init_schema() -> None:
    db_query(
        f"""CREATE TABLE IF NOT EXISTS {CONVERSAS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            card_key TEXT UNIQUE NOT NULL,
            board TEXT NOT NULL,
            status_filtro TEXT NOT NULL,
            coluna TEXT NOT NULL,
            contato TEXT,
            vendedor TEXT,
            tag_ori_original TEXT,
            tag_cam_original TEXT,
            origem_atual TEXT,
            origem_primeira_vez TEXT,
            data_primeira_vez DATE,
            mudou_de_canal BOOLEAN NOT NULL DEFAULT FALSE,
            qtd_atendimentos_historico INTEGER NOT NULL DEFAULT 0,
            tem_etiqueta_perdeu BOOLEAN NOT NULL DEFAULT FALSE,
            motivo_perda TEXT,
            esta_na_coluna_ganhou BOOLEAN NOT NULL DEFAULT FALSE,
            data_resolucao DATE,
            tamanho_conversa_chars INTEGER NOT NULL DEFAULT 0,
            atendimentos_anteriores_expandidos INTEGER NOT NULL DEFAULT 0,
            card_raw_text TEXT,
            conversation_text TEXT,
            extraido_em TIMESTAMPTZ,
            carregado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        fetch=None,
    )
    db_query(f"CREATE INDEX IF NOT EXISTS idx_{CONVERSAS_TABLE}_vendedor ON {CONVERSAS_TABLE}(vendedor)", fetch=None)
    db_query(f"CREATE INDEX IF NOT EXISTS idx_{CONVERSAS_TABLE}_coluna ON {CONVERSAS_TABLE}(coluna)", fetch=None)
    db_query(f"CREATE INDEX IF NOT EXISTS idx_{CONVERSAS_TABLE}_origem_atual ON {CONVERSAS_TABLE}(origem_atual)", fetch=None)


UPSERT_SQL = f"""
INSERT INTO {CONVERSAS_TABLE} (
    card_key, board, status_filtro, coluna, contato, vendedor,
    tag_ori_original, tag_cam_original, origem_atual, origem_primeira_vez,
    data_primeira_vez, mudou_de_canal, qtd_atendimentos_historico,
    tem_etiqueta_perdeu, motivo_perda, esta_na_coluna_ganhou, data_resolucao,
    tamanho_conversa_chars, atendimentos_anteriores_expandidos,
    card_raw_text, conversation_text, extraido_em
) VALUES (
    %(card_key)s, %(board)s, %(status_filtro)s, %(coluna)s, %(contato)s, %(vendedor)s,
    %(tag_ori_original)s, %(tag_cam_original)s, %(origem_atual)s, %(origem_primeira_vez)s,
    %(data_primeira_vez)s, %(mudou_de_canal)s, %(qtd_atendimentos_historico)s,
    %(tem_etiqueta_perdeu)s, %(motivo_perda)s, %(esta_na_coluna_ganhou)s, %(data_resolucao)s,
    %(tamanho_conversa_chars)s, %(atendimentos_anteriores_expandidos)s,
    %(card_raw_text)s, %(conversation_text)s, %(extraido_em)s
)
ON CONFLICT (card_key) DO UPDATE SET
    coluna = EXCLUDED.coluna,
    origem_atual = EXCLUDED.origem_atual,
    origem_primeira_vez = EXCLUDED.origem_primeira_vez,
    mudou_de_canal = EXCLUDED.mudou_de_canal,
    tem_etiqueta_perdeu = EXCLUDED.tem_etiqueta_perdeu,
    motivo_perda = EXCLUDED.motivo_perda,
    esta_na_coluna_ganhou = EXCLUDED.esta_na_coluna_ganhou,
    data_resolucao = EXCLUDED.data_resolucao,
    conversation_text = EXCLUDED.conversation_text,
    carregado_em = NOW()
"""


def load_from_jsonl(paths: list[Path], batch_size: int = 300) -> dict:
    stats = {"lidos": 0, "carregados": 0, "erros": 0}
    import psycopg
    from .shared import DATABASE_URL

    with psycopg.connect(DATABASE_URL) as con:
        with con.cursor() as cur:
            for path in paths:
                lote: list[dict] = []
                with path.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        stats["lidos"] += 1
                        try:
                            r = json.loads(line)
                            lote.append(parse_record(r).__dict__)
                        except Exception as e:
                            stats["erros"] += 1
                            print(f"  erro na linha {stats['lidos']} de {path.name}: {e}")
                            continue

                        if len(lote) >= batch_size:
                            cur.executemany(UPSERT_SQL, lote)
                            con.commit()
                            stats["carregados"] += len(lote)
                            print(f"  ... {stats['carregados']} carregados ate agora")
                            lote = []

                if lote:
                    cur.executemany(UPSERT_SQL, lote)
                    con.commit()
                    stats["carregados"] += len(lote)
    return stats
