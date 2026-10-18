"""Leitura (somente leitura!) do decisions.yml editado pelo usuário."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from distro_recovery.classify import DecisionConflict


class DecisionsError(ValueError):
    """decisions.yml com formato inválido."""


@dataclass(frozen=True)
class Decisions:
    include: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset()
    repos_exclude: frozenset[str] = frozenset()
    repos_gpgkey: dict[str, str] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    swaps: list[dict[str, str]] = field(default_factory=list)


def _names(data: dict[str, Any], key: str, where: str) -> frozenset[str]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DecisionsError(f"{where}{key}: esperava uma lista de strings")
    return frozenset(value)


def load_decisions(path: Path) -> Decisions:
    if not path.exists():
        return Decisions()
    data = YAML(typ="safe").load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise DecisionsError(f"{path}: esperava um mapeamento na raiz")
    repos = data.get("repos") or {}
    if not isinstance(repos, dict):
        raise DecisionsError("repos: esperava um mapeamento")
    gpgkeys = repos.get("gpgkey") or {}
    if not isinstance(gpgkeys, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in gpgkeys.items()
    ):
        raise DecisionsError("repos.gpgkey: esperava um mapeamento id -> endereço (strings)")
    renames, swaps = data.get("renames") or {}, data.get("swaps") or []
    if not isinstance(renames, dict) or not isinstance(swaps, list):
        raise DecisionsError("renames deve ser mapeamento e swaps uma lista")
    d = Decisions(
        include=_names(data, "include", ""),
        exclude=_names(data, "exclude", ""),
        repos_exclude=_names(repos, "exclude", "repos."),
        repos_gpgkey=dict(gpgkeys),
        renames={str(k): str(v) for k, v in renames.items()},
        swaps=[dict(s) for s in swaps],
    )
    both = d.include & d.exclude
    if both:
        raise DecisionConflict(f"em include e exclude ao mesmo tempo: {', '.join(sorted(both))}")
    return d
