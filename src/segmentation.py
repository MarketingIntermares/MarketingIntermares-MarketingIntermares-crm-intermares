from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .normalization import normalize_email, normalize_phone, is_truthy_text


PHONE_CANDIDATES = ["telefone", "phone", "celular", "whatsapp", "fone"]
EMAIL_CANDIDATES = ["email", "e-mail"]
PMS_CANDIDATES = ["pms", "reserva", "codigo_pms", "código_pms"]


@dataclass
class SegmentationResult:
    audience: pd.DataFrame
    excluded_members: int
    excluded_converted: int
    duplicates_removed: int


def _find_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


def prepare_base(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    out = df.copy()
    out["_source"] = source_name

    phone_col = _find_column(out, PHONE_CANDIDATES)
    email_col = _find_column(out, EMAIL_CANDIDATES)
    pms_col = _find_column(out, PMS_CANDIDATES)

    out["_phone_norm"] = out[phone_col].map(normalize_phone) if phone_col else ""
    out["_email_norm"] = out[email_col].map(normalize_email) if email_col else ""
    out["_converted"] = out[pms_col].map(is_truthy_text) if pms_col else False
    return out


def segment(
    base_1: pd.DataFrame,
    base_2: pd.DataFrame,
    members: pd.DataFrame | None = None,
) -> SegmentationResult:
    b1 = prepare_base(base_1, "Base de Leads 1")
    b2 = prepare_base(base_2, "Base de Leads 2")
    combined = pd.concat([b1, b2], ignore_index=True)

    initial = len(combined)
    converted_mask = combined["_converted"].fillna(False)
    excluded_converted = int(converted_mask.sum())
    combined = combined.loc[~converted_mask].copy()

    excluded_members = 0
    if members is not None and not members.empty:
        m = prepare_base(members, "Programa de Membros")
        member_phones = set(m["_phone_norm"]) - {""}
        member_emails = set(m["_email_norm"]) - {""}

        member_mask = combined["_phone_norm"].isin(member_phones) | combined["_email_norm"].isin(member_emails)
        excluded_members = int(member_mask.sum())
        combined = combined.loc[~member_mask].copy()

    before_dedup = len(combined)
    combined["_dedupe_key"] = combined["_phone_norm"].where(
        combined["_phone_norm"].ne(""),
        "email:" + combined["_email_norm"],
    )
    combined = combined.loc[combined["_dedupe_key"].ne("email:")].copy()
    combined = combined.drop_duplicates(subset=["_dedupe_key"], keep="first")
    duplicates_removed = before_dedup - len(combined)

    helper_cols = [c for c in combined.columns if c.startswith("_")]
    audience = combined.drop(columns=helper_cols, errors="ignore").reset_index(drop=True)

    return SegmentationResult(
        audience=audience,
        excluded_members=excluded_members,
        excluded_converted=excluded_converted,
        duplicates_removed=duplicates_removed,
    )
