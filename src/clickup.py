from __future__ import annotations

import requests

from .config import settings


class ClickUpClient:
    def __init__(self, token: str | None = None):
        self.token = token or settings.clickup_token
        self.base_url = "https://api.clickup.com/api/v2"

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token, "Content-Type": "application/json"}

    def get_task(self, task_id: str) -> dict:
        if not self.enabled:
            raise RuntimeError("CLICKUP_TOKEN não configurado.")
        response = requests.get(
            f"{self.base_url}/task/{task_id}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_task(self, list_id: str, name: str, description: str = "") -> dict:
        if not self.enabled:
            raise RuntimeError("CLICKUP_TOKEN não configurado.")
        response = requests.post(
            f"{self.base_url}/list/{list_id}/task",
            headers=self._headers(),
            json={"name": name, "description": description},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
