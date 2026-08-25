# CRM Intermares — Reconstrução V1

Base reconstruída para substituir, de forma controlada, o empacotamento atual do Railway por código-fonte versionado no GitHub.

## Escopo desta V1

- Streamlit como interface web
- autenticação com cookie persistente
- Postgres em produção e SQLite local como fallback
- segmentação por Base de Leads 1 + Base de Leads 2
- Programa de Membros usado somente para conferência/exclusão
- deduplicação por telefone e e-mail
- PMS preenchido tratado como conversão
- exportação CSV padrão Asksuite
- worker separado para rotinas agendadas
- adaptador ClickUp preparado para API
- configuração por variáveis de ambiente
- healthcheck e estrutura de testes

## Estrutura

```text
.
├── app.py
├── worker.py
├── requirements.txt
├── railway.toml
├── .env.example
├── src/
│   ├── auth.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── normalization.py
│   ├── segmentation.py
│   ├── asksuite.py
│   ├── clickup.py
│   └── sync.py
├── sql/
│   └── schema.sql
└── tests/
    ├── test_normalization.py
    └── test_segmentation.py
```

## Desenvolvimento local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

## Produção

O Railway deve apontar para este repositório somente depois da homologação.

Não commitar `.env`, tokens, senhas ou chaves.
