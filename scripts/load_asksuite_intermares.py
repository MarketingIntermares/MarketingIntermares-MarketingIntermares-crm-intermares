"""
Carga unica dos dados de agosto/2026 extraidos do Asksuite (Intermares)
para o Postgres. Le os jsonl gerados pelo projeto separado
asksuite-conversas-analytics e carrega na tabela {DB_NAMESPACE}_asksuite_conversas
(mesma tabela da Nauticomar -- ja tem coluna "board" pra distinguir).

Uso: roda direto, sem precisar exportar nada antes --
    .venv/bin/python scripts/load_asksuite_intermares.py
Se DATABASE_URL/APP_SECRET_KEY ja estiverem no ambiente (ex: rodando via
"railway run"), usa esses direto sem perguntar nada. Senao, pede pra colar
na hora (input escondido, tipo senha -- nao aparece na tela nem fica salvo
no historico do terminal).
"""

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if not os.environ.get("DATABASE_URL"):
    print("DATABASE_URL nao encontrada no ambiente.")
    print("Pega no painel do Railway: Postgres > Connect > Public Network > Add Public Access,")
    print("depois aba Variables do Postgres > copia o valor de DATABASE_PUBLIC_URL.")
    os.environ["DATABASE_URL"] = getpass.getpass("Cole aqui (nao aparece na tela) e Enter: ").strip()

if not os.environ.get("APP_SECRET_KEY"):
    print("\nAPP_SECRET_KEY nao encontrada no ambiente.")
    print("Pega rodando em outro terminal: railway run --service crm-intermares-homolog printenv APP_SECRET_KEY")
    os.environ["APP_SECRET_KEY"] = getpass.getpass("Cole aqui (nao aparece na tela) e Enter: ").strip()

from src.conversas_asksuite import init_schema, load_from_jsonl, CONVERSAS_TABLE

DATA_DIR = Path("/Users/teste1234/Documents/SISTEMAS /asksuite-conversas-analytics/data")

ARQUIVOS = [
    DATA_DIR / "intermares_aberto_202608_v3.jsonl",
    DATA_DIR / "intermares_resolvido_202608_v3.jsonl",
]


def main():
    for p in ARQUIVOS:
        if not p.exists():
            raise SystemExit(f"Arquivo nao encontrado: {p}")

    print(f"Criando/confirmando tabela {CONVERSAS_TABLE} (inclui migracao da coluna 'converteu')...")
    init_schema()

    print("Carregando dados...")
    stats = load_from_jsonl(ARQUIVOS)

    print(f"\nConcluido: {stats['lidos']} lidos, {stats['carregados']} carregados, {stats['erros']} erros.")


if __name__ == "__main__":
    main()
