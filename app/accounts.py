# create by Ortseam
"""Pembacaan akun tanpa menampilkan atau mengubah isi kata sandi."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from utils import state_file_lock


@dataclass(frozen=True)
class Account:
    identifier: str
    password: str = field(repr=False)


@state_file_lock()
def load_accounts(path: Path) -> list[Account]:
    accounts: list[Account] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, raw_line in enumerate(source, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            identifier, separator, password = line.partition("|")
            identifier = identifier.strip()
            if (
                not separator
                or not identifier
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in identifier)
                or not password
                or password.isspace()
            ):
                raise ValueError(f"Baris {line_number}: entri akun tidak valid.")
            key = identifier.casefold()
            if key in seen:
                raise ValueError(f"Baris {line_number}: akun duplikat.")
            seen.add(key)
            accounts.append(Account(identifier=identifier, password=password))
    return accounts
