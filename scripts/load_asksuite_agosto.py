"""
Carga unica dos dados de agosto/2026 extraidos do Asksuite (Nauticomar)
para o Postgres. Le os jsonl gerados pelo projeto separado
asksuite-conversas-analytics e carrega na tabela {DB_NAMESPACE}_asksuite_conversas.

Uso (via Railway, pra pegar DATABASE_URL sem expor):
    railway run --service crm-intermares-homolog python scripts/load_asksuite_agosto.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversas_asksuite import init_schema, load_from_jsonl, CONVERSAS_TABLE

DATA_DIR = Path("/Users/teste1234/Documents/SISTEMAS /asksuite-conversas-analytics/data")

ARQUIVOS = [
    DATA_DIR / "nauticomar_aberto_202608_v2.jsonl",
    DATA_DIR / "nauticomar_resolvido_202608_v2.jsonl",
]


def main():
    for p in ARQUIVOS:
        if not p.exists():
            raise SystemExit(f"Arquivo nao encontrado: {p}")

    print(f"Criando/confirmando tabela {CONVERSAS_TABLE}...")
    init_schema()

    print("Carregando dados...")
    stats = load_from_jsonl(ARQUIVOS)

    print(f"\nConcluido: {stats['lidos']} lidos, {stats['carregados']} carregados, {stats['erros']} erros.")


if __name__ == "__main__":
    main()
