"""Tipos do domínio. Sem I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

# Todos os tipos aqui são imutáveis (frozen) e sem lógica de I/O: circulam entre as camadas
# (backend -> classify -> render) sem risco de alguém alterá-los pelo caminho.


class Reason(StrEnum):
    """Valores literais de `%{reason}` / `packages[].reason` no dnf5 5.4."""

    USER = "User"
    GROUP = "Group"
    DEPENDENCY = "Dependency"
    WEAK_DEPENDENCY = "Weak Dependency"
    CLEAN = "Clean"


class Confidence(StrEnum):
    """Destino de um pacote: HIGH -> fedora.yml, REVIEW -> pending.yml, EXCLUDED -> só log."""

    HIGH = "high"
    REVIEW = "review"
    EXCLUDED = "excluded"


class ReasonCode(StrEnum):
    """Por que o classificador decidiu assim (uma linha da tabela de decisão do CLAUDE.md)."""

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
    """Como o repositório é recriado no restore (decidido pelo dono do arquivo .repo)."""

    OFFICIAL = "official"
    THIRD_PARTY = "third-party"
    RELEASE_PACKAGE = "release-package"
    COPR = "copr"
    REPOFILE = "repofile"


@dataclass(frozen=True)
class InstalledPkg:
    """Uma linha do `dnf5 repoquery --installed`."""

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
    """Resultado do classificador para um nome de pacote."""

    name: str
    confidence: Confidence
    reason_code: ReasonCode
    group: str | None = None  # preenchido em via_group


@dataclass(frozen=True)
class Group:
    """Grupo de pacotes do DNF (ex.: development-tools) e seus pacotes padrão."""

    id: str
    name: str
    packages: frozenset[str]  # mandatory + default


@dataclass(frozen=True)
class Baseline:
    """O que a instalação "de fábrica" já traz; o que está aqui nunca é escolha do usuário."""

    environment: str
    groups: frozenset[str]
    packages: frozenset[str]
    protected: frozenset[str]
    system_release: frozenset[str]  # pacotes que fornecem `system-release`


@dataclass(frozen=True)
class RepoSpec:
    """Receita para recriar um repositório de terceiros em outra máquina."""

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
    """name-epoch:version-release.arch, as partes de um pacote RPM."""

    name: str
    epoch: str
    version: str
    release: str
    arch: str


# "nome-epoch:ver-rel.arch". O nome pode ter hífens, por isso o `.+` guloso no começo.
_NEVRA = re.compile(
    r"^(?P<name>.+)-(?P<epoch>\d+):(?P<version>[^-:]+)-(?P<release>[^-]+)\.(?P<arch>[^.-]+)$"
)


def parse_nevra(nevra: str) -> Nevra:
    m = _NEVRA.match(nevra)
    if m is None:
        raise ValueError(f"nevra inválido: {nevra!r}")
    return Nevra(**m.groupdict())


@dataclass(frozen=True)
class UserHistory:
    """Transações do histórico que instalaram pacotes com reason=User."""

    image_tx_ids: frozenset[int]  # builds da imagem de instalação: baseline, não ação do usuário
    installs: dict[str, frozenset[int]]  # nome -> ids das transações
    image_packages: frozenset[str] = frozenset()  # instalados (qualquer razão) pela imagem
