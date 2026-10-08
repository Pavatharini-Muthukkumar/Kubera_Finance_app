"""Settings: secrets from the environment, personal details from a private TOML file.

Nothing personal lives in the code. Your names, IBANs and the street names to
strip from merchant text go in ``kubera.toml`` (git-ignored); see
``kubera.example.toml``.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    owner_names: list[str] = field(default_factory=list)
    owner_ibans: set[str] = field(default_factory=set)
    account_names: dict[str, str] = field(default_factory=dict)
    noise_patterns: list[str] = field(default_factory=list)
    gemini_model: str = "gemini-3.8-flash"
    gemini_batch_size: int = 40
    state_dir: Path = Path(".kubera")

    def account_name(self, iban: str, bank: str) -> str:
        if iban in self.account_names:
            return self.account_names[iban]
        return f"{bank} …{iban[-4:]}" if iban else bank

    def owner_regex(self) -> re.Pattern | None:
        """Matches any of the owner's names, in either word order."""
        variants = []
        for name in self.owner_names:
            parts = [p for p in re.split(r"[\s,]+", name) if p]
            if not parts:
                continue
            variants.append(r"[\s,]+".join(map(re.escape, parts)))
            if len(parts) == 2:
                variants.append(r"[\s,]+".join(map(re.escape, parts[::-1])))
        return re.compile("|".join(variants), re.I) if variants else None


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.getenv("KUBERA_CONFIG", "kubera.toml"))
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    owner = data.get("owner", {})
    gemini = data.get("gemini", {})
    return Config(
        owner_names=list(owner.get("names", [])),
        owner_ibans={re.sub(r"\s+", "", i).upper() for i in owner.get("ibans", [])},
        account_names={re.sub(r"\s+", "", k).upper(): v for k, v in data.get("accounts", {}).items()},
        noise_patterns=list(data.get("cleaning", {}).get("noise_patterns", [])),
        gemini_model=os.getenv("KUBERA_GEMINI_MODEL", gemini.get("model", "gemini-3.8-flash")),
        gemini_batch_size=int(gemini.get("batch_size", 40)),
        state_dir=Path(os.getenv("KUBERA_STATE_DIR", data.get("state_dir", ".kubera"))),
    )
