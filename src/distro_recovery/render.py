"""YAML determinístico: ordem fixa de chaves, listas ordenadas, strings entre aspas."""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from distro_recovery.model import Classified, RepoSpec

HEADER = "# Gerado por distro-recovery. Não edite: este arquivo é reescrito a cada backup.\n"


def _quote(obj: Any) -> Any:
    if isinstance(obj, str):
        return DoubleQuotedScalarString(obj)
    if isinstance(obj, Mapping):
        return {k: _quote(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_quote(v) for v in obj]
    return obj


def _dump(data: Mapping[str, Any]) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 100_000
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = io.StringIO()
    yaml.dump(_quote(data), buf)
    return HEADER + buf.getvalue()


def _repo(r: RepoSpec, *, warnings: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": r.id, "kind": r.kind.value, "name": r.name}
    optional: list[tuple[str, Any]] = [
        ("baseurl", r.baseurl),
        ("owner", r.owner),
        ("package", r.package),
        ("gpgkeys", sorted(r.gpgkeys)),
        ("gpgkey_urls", sorted(r.gpgkey_urls)),
    ]
    if warnings:
        optional.append(("warnings", sorted(r.warnings)))
    entry.update({k: v for k, v in optional if v})
    return entry


def render_fedora(packages: Iterable[str], groups: Iterable[str], repos: Iterable[RepoSpec]) -> str:
    return _dump(
        {
            "family": "fedora",
            "repos": [_repo(r, warnings=False) for r in sorted(repos, key=lambda r: r.id)],
            "groups": sorted(set(groups)),
            "packages": sorted(set(packages)),
        }
    )


def render_pending(
    packages: Iterable[Classified], repos: Iterable[RepoSpec], missing_repos: Iterable[str]
) -> str:
    items = sorted(packages, key=lambda c: (c.name, c.reason_code.value))
    return _dump(
        {
            "packages": [{"name": c.name, "reason_code": c.reason_code.value} for c in items],
            "repos": [_repo(r, warnings=True) for r in sorted(repos, key=lambda r: r.id)],
            "missing_repos": sorted(set(missing_repos)),
        }
    )


def render_meta(
    *, family: str, tool_version: str, generated_at: str, counts: Mapping[str, int]
) -> str:
    return _dump(
        {
            "family": family,
            "tool_version": tool_version,
            "generated_at": generated_at,
            "counts": {k: counts[k] for k in sorted(counts)},
        }
    )
