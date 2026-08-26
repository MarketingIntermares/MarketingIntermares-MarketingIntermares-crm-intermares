# CRM Intermares V2

V2 reconstruída a partir do código real recuperado do `crm-intermares-web` e do `crm-intermares-worker`.

## O que foi preservado
- autenticação persistente
- usuários e níveis de acesso
- troca de senha
- token ClickUp criptografado
- segmentação nas duas bases
- exclusão de Programa de Membros
- classificação PMS/check-in/check-out
- prevenção de conflitos entre departamentos
- geração de CSV
- execução da campanha no ClickUp
- worker seg/qua/sex e alternância Tamara/Márcio

## Correção estrutural principal
Web e worker agora usam o mesmo módulo compartilhado para:
- criptografia do token ClickUp
- schema de banco
- KV state
- logs de campanha

Em homologação use `DB_NAMESPACE=crm_v2` para não misturar as tabelas com a produção atual.

## Segurança
Os arquivos recuperados originais estão no `.gitignore` e não devem ser commitados.
