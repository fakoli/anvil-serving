"""Protected bank grants shared by Connect onboarding and the memory router."""
from __future__ import annotations

import os
import re
import stat

from .control_plane.bootstrap_shim import open_trusted_file
from .connect.config import _json_load

SCHEMA = "anvil-memory-access/v1"
OPERATIONS = {"retain", "recall", "reflect"}
BANK = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}$")
HUMAN = re.compile(r"human:[0-9a-f]{64}$")
MAX_BYTES = 1024 * 1024


def validate(value: object) -> dict:
    if (type(value) is not dict or set(value) != {"schema", "users"}
            or value["schema"] != SCHEMA or type(value["users"]) is not dict
            or len(value["users"]) > 4096):
        raise ValueError("invalid memory access policy")
    for principal, grant in value["users"].items():
        if (not HUMAN.fullmatch(principal) or type(grant) is not dict
                or set(grant) != {"default_bank", "admin", "banks"}
                or type(grant["admin"]) is not bool or type(grant["banks"]) is not dict
                or not 1 <= len(grant["banks"]) <= 128
                or type(grant["default_bank"]) is not str
                or grant["default_bank"] not in grant["banks"]):
            raise ValueError("invalid memory access grant")
        for bank, operations in grant["banks"].items():
            if (not BANK.fullmatch(bank) or type(operations) is not list or not operations
                    or any(type(op) is not str or op not in OPERATIONS for op in operations)
                    or len(operations) != len(set(operations))):
                raise ValueError("invalid memory bank grant")
    return value


def read(path: str) -> dict:
    def permissions(fd):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o037):
            raise ValueError("unsafe memory access policy")
    with open_trusted_file(path, max_bytes=MAX_BYTES, require_readonly=False) as opened:
        return validate(_json_load(opened.read_verified(permissions).decode("utf-8")))


def resolve(path: str, principal: str, operation: str, bank: str | None = None) -> tuple[str, dict]:
    grant = read(path)["users"].get(principal)
    if grant is None:
        raise PermissionError("memory access is not provisioned")
    selected = grant["default_bank"] if bank is None else bank
    if type(selected) is not str or not BANK.fullmatch(selected):
        raise ValueError("invalid memory bank")
    if operation != "banks" and not grant["admin"] and operation not in grant["banks"].get(selected, []):
        raise PermissionError("memory bank is not granted")
    return selected, grant
