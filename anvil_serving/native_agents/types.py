"""Closed, portable contracts for the native Agents broker."""
from dataclasses import dataclass
from pathlib import Path
import re

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}\Z")
PRINCIPAL = re.compile(r"human:[0-9a-f]{64}\Z")

@dataclass(frozen=True)
class NativeAdmission:
    principal: str
    generation: int
    expires_at: float
    resource: str = "native-agents"
    credential_id: str = ""

@dataclass(frozen=True)
class Directory:
    id: str
    label: str
    path: Path
    display_path: str
    runtime_path: str | None = None

@dataclass(frozen=True)
class Workspace:
    id: str
    principal: str
    label: str
    directories: tuple[Directory, ...]
    owner_url: str
    owner_token_file: Path
    isolation: str = "personal"

    def public(self):
        return {"id": self.id, "label": self.label, "isolation": self.isolation,
                "directories": [{"id": d.id, "label": d.label, "displayPath": d.display_path} for d in self.directories]}

class AgentsError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code
