from __future__ import annotations

from io import StringIO
import pandas as pd

from .normalization import normalize_email, normalize_phone


def assign_vendors(df: pd.DataFrame, vendor_a: str, vendor_b: str) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    out["vendedor"] = [vendor_a if i % 2 == 0 else vendor_b for i in range(len(out))]
    return out


def to_asksuite_csv(df: pd.DataFrame, campaign_name: str) -> bytes:
    out = df.copy()

    phone_col = next((c for c in out.columns if str(c).strip().lower() in {"telefone", "phone", "celular", "whatsapp", "fone"}), None)
    email_col = next((c for c in out.columns if str(c).strip().lower() in {"email", "e-mail"}), None)

    export = pd.DataFrame()
    export["nome"] = out.get("nome", out.get("name", ""))
    export["telefone"] = out[phone_col].map(normalize_phone) if phone_col else ""
    export["email"] = out[email_col].map(normalize_email) if email_col else ""
    export["campanha"] = campaign_name
    if "vendedor" in out.columns:
        export["vendedor"] = out["vendedor"]

    buffer = StringIO()
    export.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8-sig")
