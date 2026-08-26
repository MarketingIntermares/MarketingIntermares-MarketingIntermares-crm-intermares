"""
Roda o mesmo fluxo de login/MFA da pagina Asksuite_Sync.py, mas por
terminal -- util pra validar sem precisar clicar na UI.

A senha ja esta salva (criptografada) no Postgres por quem preencheu o
formulario antes; esse script so descriptografa e usa internamente
(igual a pagina Streamlit ja faz), nunca imprime a senha.

Uso (via Railway, pra ter DATABASE_URL):
    railway run --service crm-intermares-homolog .venv/bin/python scripts/debug_asksuite_login.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone

from src.asksuite_api import AsksuiteClient, AsksuiteError, pick_access_token, pick_refresh_token
from src.shared import get_secret, save_secret


def main():
    email = get_secret("asksuite_email")
    password = get_secret("asksuite_password")
    api_key = get_secret("asksuite_api_key")

    if not email or not password:
        raise SystemExit("Faltam credenciais salvas (asksuite_email / asksuite_password).")

    print(f"Email carregado: {email}")
    print(f"API key carregada: {'sim' if api_key else 'NAO -- falta salvar'}")
    print("Chamando POST /v1/auth/login...")

    client = AsksuiteClient()
    try:
        result = client.login(email, password)
    except AsksuiteError as e:
        print(f"Login falhou: {e}")
        return

    token = pick_access_token(result)
    if token:
        print("Login OK, accessToken recebido direto (sem MFA).")
    elif result.get("mfaRequired") or result.get("mfa_required"):
        print(f"MFA solicitado (tipo: {result.get('mfaType') or result.get('mfa_type')}).")
        code = input("Codigo recebido por email: ").strip()
        try:
            result = client.verify_mfa(email, password, code)
        except AsksuiteError as e:
            print(f"Verificacao MFA falhou: {e}")
            return
        token = pick_access_token(result)
        if not token:
            print("MFA respondeu 200 mas sem accessToken reconhecivel. Resposta:")
            print(result)
            return
        print("MFA confirmado, accessToken recebido.")
    else:
        print("Login respondeu sem accessToken e sem MFA reconhecido. Resposta:")
        print(result)
        return

    refresh = pick_refresh_token(result)
    save_secret("asksuite_access_token", token, "debug_script")
    if refresh:
        save_secret("asksuite_refresh_token", refresh, "debug_script")
    save_secret("asksuite_token_verified_at", datetime.now(timezone.utc).isoformat(), "debug_script")
    print("accessToken salvo em crm_v2_secrets.")

    if api_key:
        print("\nTestando GET /v1/companies...")
        try:
            companies = AsksuiteClient(api_key=api_key, access_token=token).companies()
            print(f"OK: {len(companies)} empresa(s) retornada(s).")
            for c in companies[:10]:
                print(" ", c)
        except AsksuiteError as e:
            print(f"Teste de conexao falhou: {e}")


if __name__ == "__main__":
    main()
