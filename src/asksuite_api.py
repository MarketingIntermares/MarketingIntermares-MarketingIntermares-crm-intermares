from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import requests

class AsksuiteError(RuntimeError):
    pass

def _pick(payload: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("data","result","auth"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _pick(nested, keys)
            if found:
                return found
    return ""

def pick_access_token(payload: Any) -> str:
    return _pick(payload, ("accessToken","access_token","token","jwt"))

def pick_refresh_token(payload: Any) -> str:
    return _pick(payload, ("refreshToken","refresh_token"))

def extract_items(payload: Any, preferred: tuple[str, ...]=()) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in preferred + ("items","results","content","rows","data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = extract_items(value, preferred)
            if nested:
                return nested
    return []

@dataclass
class AsksuiteClient:
    api_key: str = ""
    access_token: str = ""
    base_url: str = "https://api.asksuite.com"
    timeout: int = 60

    def _headers(self, authenticated: bool=True) -> dict[str,str]:
        headers = {"Content-Type":"application/json","Accept":"application/json"}
        if authenticated:
            if self.api_key.strip():
                headers["X-API-Key"] = self.api_key.strip()
            if self.access_token.strip():
                headers["Authorization"] = f"Bearer {self.access_token.strip()}"
        return headers

    def _request(self, method: str, path: str, *, authenticated: bool=True, **kwargs):
        response = requests.request(
            method,
            f"{self.base_url.rstrip('/')}{path}",
            headers=self._headers(authenticated),
            timeout=self.timeout,
            **kwargs,
        )
        if response.status_code == 401:
            raise AsksuiteError("401 Não autorizado — credenciais inválidas ou token expirado")
        if response.status_code >= 400:
            raise AsksuiteError(f"Asksuite {response.status_code}: {response.text[:500]}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise AsksuiteError(f"Resposta Asksuite não é JSON válido: {response.text[:300]}") from exc

    def login(self, email: str, password: str) -> dict:
        payload = self._request(
            "POST","/v1/auth/login",authenticated=False,
            json={"email":email.strip(),"password":password}
        )
        return payload if isinstance(payload,dict) else {"raw":payload}

    def verify_mfa(self, email: str, password: str, code: str) -> dict:
        payload = self._request(
            "POST","/v1/auth/login/verify",authenticated=False,
            json={"email":email.strip(),"password":password,"code":code.strip()}
        )
        return payload if isinstance(payload,dict) else {"raw":payload}

    def companies(self) -> list[dict]:
        return extract_items(self._request("GET","/v1/companies"),("companies",))

    def tags(self) -> list[dict]:
        return extract_items(self._request("GET","/v1/tags"),("tags",))

    def attendances(self, body: dict|None=None):
        raw = self._request("POST","/v1/attendances",json=body or {})
        return extract_items(raw,("attendances",)), raw if isinstance(raw,dict) else {"data":raw}

    def attendance_history(self, attendance_id: str):
        raw = self._request("GET",f"/v1/attendances/{attendance_id}/history")
        return extract_items(raw,("history","messages","events")), raw if isinstance(raw,dict) else {"data":raw}
