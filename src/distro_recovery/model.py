"""Tipos do domínio. Sem I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple


class Reason(StrEnum):
    """Valores literais de `%{reason}` / `packages[].reason` no dnf5 5.4."""

    USER = "User"
    GROUP = "Group"
    DEPENDENCY = "Dependency"
    WEAK_DEPENDENCY = "Weak Dependency"
    CLEAN = "Clean"


class Confidence(StrEnum):
    HIGH = "high"
    REVIEW = "review"
    EXCLUDED = "excluded"


class ReasonCode(StrEnum):
    USER_EXCLUDED = "user_excluded"
    USER_INCLUDED = "user_included"
    SYSTEM_RULE = "system_rule"
    DEPENDENCY = "dependency"
    VIA_GROUP = "via_group"
    BASELINE_GROUP = "baseline_group"
    USER_HISTORY = "user_history"
    USER_LEAF = "user_leaf"
    USER_UNCONFIRMED = "user_unconfirmed"
    IN_BASELINE_BUT_USER = "in_baseline_but_user"
    BASELINE = "baseline"
    LOCAL_RPM = "local_rpm"
    DUPLICATED_IN_BREW = "duplicated_in_brew"


class RepoKind(StrEnum):
    OFFICIAL = "official"
    THIRD_PARTY = "third-party"
    RELEASE_PACKAGE = "release-package"
    COPR = "copr"
    REPOFILE = "repofile"


@dataclass(frozen=True)
class InstalledPkg:
    name: str
    arch: str
    reason: Reason
    from_repo: str
    vendor: str

    @property
    def is_local(self) -> bool:
        return self.from_repo == "@commandline"


@dataclass(frozen=True)
class Classified:
    name: str
    confidence: Confidence
    reason_code: ReasonCode
    group: str | None = None  # preenchido em via_group


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    packages: frozenset[str]  # mandatory + default


@dataclass(frozen=True)
class Baseline:
    environment: str
    groups: frozenset[str]
    packages: frozenset[str]
    protected: frozenset[str]
    system_release: frozenset[str]  # pacotes que fornecem `system-release`


@dataclass(frozen=True)
class RepoSpec:
    id: str
    kind: RepoKind
    name: str
    baseurl: str | None = None
    gpgkeys: tuple[str, ...] = ()  # fingerprints
    gpgkey_urls: tuple[str, ...] = ()
    owner: str | None = None  # copr: "dono/projeto"
    package: str | None = None  # dono do .repo (release-package)
    warnings: tuple[str, ...] = ()


class Nevra(NamedTuple):
    name: str
    epoch: str
    version: str
    release: str
    arch: str


_NEVRA = re.compile(
    r"^(?P<name>.+)-(?P<epoch>\d+):(?P<version>[^-:]+)-(?P<release>[^-]+)\.(?P<arch>[^.-]+)$"
)


def parse_nevra(nevra: str) -> Nevra:
    m = _NEVRA.match(nevra)
    if m is None:
        raise ValueError(f"nevra inválido: {nevra!r}")
    return Nevra(**m.groupdict())
