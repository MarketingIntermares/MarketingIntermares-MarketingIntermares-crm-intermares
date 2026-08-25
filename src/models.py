from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Lead:
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    source: str = ""
    pms: str = ""
    last_contact: Optional[str] = None
    last_stay: Optional[str] = None
