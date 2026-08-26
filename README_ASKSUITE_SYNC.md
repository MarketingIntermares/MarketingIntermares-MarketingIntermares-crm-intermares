# Asksuite Sync V1 — homologação

Módulo de sincronização Asksuite → ClickUp em **modo seguro**.

## Implementado
- autenticação BETA da Asksuite:
  - `POST /v1/auth/login`
  - MFA por e-mail
  - `POST /v1/auth/login/verify`
  - armazenamento criptografado do `accessToken`
- teste em `GET /v1/companies`
- leitura de `POST /v1/attendances`
- normalização tolerante do payload BETA
- roteamento Comercial / Pós-vendas / Descarte
- índice persistente ClickUp no Postgres
- reindexação completa por janela mensal nas três listas
- delta por `date_updated_gt`
- correspondência por e-mail e telefone
- proteção contra regressão e status terminal
- simulação tabular com métricas
- log de execução no Postgres

## Segurança
Esta V1 não possui caminho de escrita real no ClickUp. O objetivo é validar:
1. autenticação Asksuite;
2. shape real do `/v1/attendances`;
3. taxa de match do índice;
4. regras de roteamento.

A escrita só deve ser liberada depois da validação do modo seguro.
