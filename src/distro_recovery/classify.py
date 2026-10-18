"""Tabela de decisão. Pura: sem I/O, sem relógio, sem ambiente."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from distro_recovery.model import (
    Baseline,
    Classified,
    Confidence,
    Group,
    InstalledPkg,
    Reason,
    ReasonCode,
    RepoKind,
    RepoSpec,
    UserHistory,
)


class DecisionConflict(ValueError):
    """Um nome em include e exclude ao mesmo tempo."""


@dataclass(frozen=True)
class Context:
    include: frozenset[str]
    exclude: frozenset[str]
    baseline: Baseline
    groups: list[Group]  # grupos instalados
    history: UserHistory
    leaves: frozenset[str]
    brew: frozenset[str]

    def __post_init__(self) -> None:
        both = self.include & self.exclude
        if both:
            raise DecisionConflict(
                f"em include e exclude ao mesmo tempo: {', '.join(sorted(both))}"
            )


# kmod-<nome>-<versão do kernel>-<release>: versionado (akmod-* e `kmod-libs` não casam)
_VERSIONED_KMOD = re.compile(r"^kmod-.+-\d+(\.\d+)+-\d+")


def is_system_rule(name: str, baseline: Baseline) -> bool:
    return (
        name.startswith("kernel")
        or _VERSIONED_KMOD.match(name) is not None
        or name.endswith("-firmware")
        or name.startswith("glibc-langpack-")
        or name.startswith("anaconda")
        or name in baseline.system_release
        or name in baseline.protected
    )


def _decide(pkg: InstalledPkg, ctx: Context) -> Classified:
    name = pkg.name
    H, R, X = Confidence.HIGH, Confidence.REVIEW, Confidence.EXCLUDED  # noqa: N806

    def out(conf: Confidence, code: ReasonCode, group: str | None = None) -> Classified:
        return Classified(name, conf, code, group)

    if name in ctx.exclude:
        return out(X, ReasonCode.USER_EXCLUDED)
    if name in ctx.include:
        return out(H, ReasonCode.USER_INCLUDED)
    if is_system_rule(name, ctx.baseline):
        return out(X, ReasonCode.SYSTEM_RULE)
    if pkg.reason in (Reason.DEPENDENCY, Reason.WEAK_DEPENDENCY, Reason.CLEAN):
        return out(X, ReasonCode.DEPENDENCY)

    in_baseline = name in ctx.baseline.packages
    if pkg.reason is Reason.GROUP:
        if in_baseline:
            return out(X, ReasonCode.BASELINE_GROUP)
        for g in sorted(ctx.groups, key=lambda g: g.id):
            if g.id not in ctx.baseline.groups and name in g.packages:
                return out(X, ReasonCode.VIA_GROUP, g.id)
        # veio de um grupo que não conhecemos (removido?): não dá para cobrir nem descartar
        return out(R, ReasonCode.USER_UNCONFIRMED)

    txs = ctx.history.installs.get(name, frozenset()) - ctx.history.image_tx_ids
    if in_baseline:
        if txs:
            result = out(R, ReasonCode.IN_BASELINE_BUT_USER)
        else:
            return out(X, ReasonCode.BASELINE)
    elif txs:
        result = out(H, ReasonCode.USER_HISTORY)
    elif name in ctx.leaves:
        result = out(H, ReasonCode.USER_LEAF)
    else:
        result = out(R, ReasonCode.USER_UNCONFIRMED)

    # candidatos a entrar no backup, mas que pedem olhar humano
    if pkg.is_local:
        return out(R, ReasonCode.LOCAL_RPM)
    if name in ctx.brew:
        return out(R, ReasonCode.DUPLICATED_IN_BREW)
    return result


def classify(pkg: InstalledPkg, ctx: Context) -> Classified:
    return _decide(pkg, ctx)


def classify_all(pkgs: Iterable[InstalledPkg], ctx: Context) -> list[Classified]:
    """Um resultado por nome (várias versões/arquiteturas colapsam), ordenado."""
    first: dict[str, InstalledPkg] = {}
    for p in sorted(pkgs, key=lambda p: (p.name, p.arch, p.from_repo)):
        first.setdefault(p.name, p)
    return [classify(p, ctx) for p in first.values()]


# avisos que impedem um repositório de ir direto para o backup
_BLOCKING_WARNINGS = ("credentials_in_url", "gpgcheck_disabled", "local_gpgkey")


def split_repos(repos: Iterable[RepoSpec]) -> tuple[list[RepoSpec], list[RepoSpec]]:
    """(vão para generated, vão para pending). Repositórios oficiais são descartados."""
    ok, pending = [], []
    for r in repos:
        if r.kind is RepoKind.OFFICIAL:
            continue
        blocked = any(w.split(":")[0] in _BLOCKING_WARNINGS for w in r.warnings)
        (pending if blocked else ok).append(r)
    return ok, pending
