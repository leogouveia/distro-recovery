"""Backend Fedora / DNF5. Toda leitura passa pelo runner injetado."""

from __future__ import annotations

import configparser
import json
import re
from collections.abc import Iterable
from dataclasses import replace

from distro_recovery.backends.base import CmdResult, Runner
from distro_recovery.model import (
    Baseline,
    Group,
    InstalledPkg,
    Reason,
    RepoKind,
    RepoSpec,
    UserHistory,
    parse_nevra,
)

DNF = ["dnf5", "--assumeno"]  # --assumeno: nunca aceitar import de chave GPG
QUERYFORMAT = "%{name}\t%{arch}\t%{reason}\t%{from_repo}\t%{vendor}\n"  # TAB/LF reais
REPO_DIR = "/etc/yum.repos.d"
PROTECTED_DIR = "/etc/dnf/protected.d"
ARCHES = ("x86_64", "aarch64", "i686", "ppc64le", "s390x")


# ---------------------------------------------------------------- parsers


def parse_installed(text: str) -> list[InstalledPkg]:
    """Saída do repoquery (5 colunas separadas por TAB) -> lista de InstalledPkg."""
    pkgs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            raise ValueError(f"esperava 5 campos separados por TAB, veio {len(fields)}: {line!r}")
        name, arch, reason, from_repo, vendor = fields
        try:
            reason_enum = Reason(reason)
        except ValueError:
            raise ValueError(f"reason desconhecido {reason!r} em {name}") from None
        pkgs.append(InstalledPkg(name, arch, reason_enum, from_repo, vendor))
    return pkgs


# "Chave : valor" (início de campo) e "      : valor" (continuação de uma lista)
_KEY = re.compile(r"^(\S.*?)\s+:\s?(.*)$")
_CONT = re.compile(r"^\s+:\s?(.*)$")


def parse_info_blocks(text: str) -> list[dict[str, list[str]]]:
    """`group info` / `environment info`: blocos `Chave : valor` com continuações `   : valor`."""
    blocks: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    key = ""
    for line in text.splitlines():
        if m := _CONT.match(line):  # continuação: acrescenta ao último campo visto
            if current is not None and key:
                current[key].append(m.group(1).strip())
        elif m := _KEY.match(line):
            key = m.group(1).strip()
            if key == "Id" or current is None:  # "Id" abre um novo bloco (grupo/ambiente)
                current = {}
                blocks.append(current)
            current[key] = [m.group(2).strip()] if m.group(2).strip() else []
    return blocks


def parse_list_ids(text: str) -> list[str]:
    """Ids da tabela de `group list` / `environment list` com Installed=yes."""
    ids = []
    for line in text.splitlines():
        # o ID pode ficar a um único espaço do nome quando preenche a coluna
        m = re.match(r"^(\S+)\s+(.*?)\s+(yes|no)$", line.strip(), re.IGNORECASE)
        if m and m.group(1) != "ID" and m.group(3).lower() == "yes":
            ids.append(m.group(1))
    return ids


def parse_leaves(text: str) -> frozenset[str]:
    """Nomes dos pacotes "folha" (nada depende deles) = candidatos a escolha do usuário."""
    names = set()
    for line in text.splitlines():
        # `- nevra` por leaf; membros de ciclos de dependência vêm só indentados
        if line.startswith("- ") or line.startswith("  "):
            names.add(parse_nevra(line.strip().removeprefix("- ")).name)
    return frozenset(names)


# from_repo de 32 hex = mídia/imagem de instalação (ver CLAUDE.md, "Divergências observadas")
_IMAGE_REPO = re.compile(r"^[0-9a-f]{32}$")


def _is_image_tx(tx: dict) -> bool:
    """Build da imagem (kiwi/Anaconda): todos os pacotes vêm do repo-hash da mídia."""
    pkgs = tx.get("packages", [])
    return bool(pkgs) and all(_IMAGE_REPO.match(p.get("repository", "")) for p in pkgs)


def _is_yes(value: str) -> bool:
    return value.strip().lower() in ("yes", "true")


def _name_of_nvra(nvra: str) -> str:
    """Ex.: bash-5.2-1.fc44.x86_64 -> bash (remove versão, release e arquitetura)."""
    m = re.match(r"^(.+)-[^-]+-[^-]+\.[^.-]+$", nvra.strip())
    if m is None:
        raise ValueError(f"nvra inválido: {nvra!r}")
    return m.group(1)


def _packages_of(info: dict[str, list[str]]) -> frozenset[str]:
    return frozenset(info.get("Mandatory packages", []) + info.get("Default packages", []))


def normalize_url(url: str, releasever: str) -> str:
    """Versões fixas -> $releasever, arquitetura -> $basearch."""
    # os lookarounds evitam trocar trechos no meio de palavras (ex.: "x86_64" dentro de um nome)
    arch = "|".join(re.escape(a) for a in ARCHES)
    url = re.sub(rf"(?<![A-Za-z0-9_.])({arch})(?![A-Za-z0-9_.])", "$basearch", url)
    return re.sub(rf"(?<![\d.]){re.escape(releasever)}(?!\d|\.\d)", "$releasever", url)


def _has_credentials(url: str) -> bool:
    return re.search(r"://[^/\s@]+@", url) is not None


def parse_rpmkeys(text: str) -> list[tuple[str, str]]:
    """`rpmkeys --list`: `<fingerprint> <uid> public key` -> (fingerprint em minúsculas, uid)."""
    keys = []
    for line in text.splitlines():
        fp, _, uid = line.strip().partition(" ")
        if fp and uid:
            keys.append((fp.lower(), uid))
    return keys


def _copr_fingerprints(owner_project: str | None, keys: list[tuple[str, str]]) -> tuple[str, ...]:
    """Chaves de COPR têm UID `<dono#projeto@copr.fedorahosted.org>`: casamento exato."""
    if not owner_project or "/" not in owner_project:
        return ()
    owner, project = owner_project.split("/", 1)
    marker = f"<{owner}#{project}@copr.fedorahosted.org>"
    return tuple(sorted(fp for fp, uid in keys if marker in uid))


def parse_repo_file(text: str) -> dict[str, dict[str, str]]:
    """Arquivo .repo (formato INI) -> {id_do_repo: {opção: valor}}."""
    cp = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read_string(text)
    return {s: dict(cp.items(s)) for s in cp.sections()}


# ---------------------------------------------------------------- backend


class Dnf5Backend:
    """Implementa o Protocol Backend para Fedora. Não sabe se o runner é real ou de fixtures."""

    family = "fedora"

    def __init__(self, runner: Runner) -> None:
        self.run = runner

    def _out(self, argv: list[str]) -> str:
        """Roda o comando e devolve o stdout; código de saída diferente de 0 vira erro."""
        res: CmdResult = self.run(argv)
        if res.returncode != 0:
            raise RuntimeError(f"{' '.join(argv[:4])}… terminou com código {res.returncode}")
        return res.stdout

    def detect(self) -> bool:
        """É Fedora? (o pacote que provê `system-release` se chama fedora-release-*)"""
        res = self.run(["rpm", "-q", "--whatprovides", "system-release"])
        return res.returncode == 0 and res.stdout.startswith("fedora-release")

    def installed(self) -> list[InstalledPkg]:
        """Todos os pacotes instalados, com o motivo (reason) e o repositório de origem."""
        return parse_installed(
            self._out([*DNF, "repoquery", "--installed", "--queryformat", QUERYFORMAT])
        )

    def _group(self, group_id: str) -> Group:
        """Detalhes de um grupo via `group info` (nome + pacotes mandatory/default)."""
        blocks = parse_info_blocks(self._out([*DNF, "group", "info", group_id]))
        info = blocks[0]
        return Group(group_id, info.get("Name", [group_id])[0], _packages_of(info))

    def installed_groups(self) -> list[Group]:
        """Grupos instalados, ordenados por id."""
        ids = parse_list_ids(self._out([*DNF, "group", "list", "--installed"]))
        return [self._group(i) for i in sorted(ids)]

    def _protected(self) -> frozenset[str]:
        """Pacotes que o DNF não deixa remover (/etc/dnf/protected.d/*.conf, um nome por linha)."""
        names: set[str] = set()
        for fname in self._out(["ls", "-1", PROTECTED_DIR]).split():
            if not fname.endswith(".conf"):
                continue
            for line in self._out(["cat", f"{PROTECTED_DIR}/{fname}"]).splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    names.add(line)
        return frozenset(names)

    def baseline(self) -> Baseline:
        """Calcula a baseline: ambiente instalado -> grupos requeridos -> pacotes desses grupos.

        `group list --installed` não basta (lista poucos grupos), por isso o caminho passa pelo
        `environment info`.
        """
        envs = parse_list_ids(self._out([*DNF, "environment", "list", "--installed"]))
        groups: set[str] = set()
        for env_id in envs:
            blocks = parse_info_blocks(self._out([*DNF, "environment", "info", env_id]))
            installed = [
                b for b in blocks if b.get("Installed", [""])[0].lower() in ("true", "yes")
            ]
            if not installed:
                raise ValueError(f"environment {env_id}: nenhuma entrada instalada")
            groups.update(installed[0].get("Required groups", []))
        packages: set[str] = set()
        for gid in sorted(groups):
            packages |= self._group(gid).packages
        provides = self._out(["rpm", "-q", "--whatprovides", "system-release"])
        return Baseline(
            environment="+".join(sorted(envs)),
            groups=frozenset(groups),
            packages=frozenset(packages),
            protected=self._protected(),
            system_release=frozenset(_name_of_nvra(x) for x in provides.split()),
        )

    def history_user_installs(self) -> UserHistory:
        """Lê o histórico do DNF: quais pacotes o usuário instalou, e em quais transações.

        Transações de build da imagem são separadas (image_tx_ids) para não contarem como ação
        do usuário. Transações que falharam (status != Ok) são ignoradas.
        """
        listing = json.loads(self._out([*DNF, "history", "list", "--json"]))
        ids = [str(h["id"]) for h in listing]
        if not ids:
            return UserHistory(image_tx_ids=frozenset(), installs={})
        txs = json.loads(self._out([*DNF, "history", "info", *ids, "--json"]))
        installs: dict[str, set[int]] = {}
        image_packages: set[str] = set()
        for tx in txs:
            if tx.get("status") != "Ok":
                continue
            for pkg in tx.get("packages", []):
                if pkg["action"] == "Install" and _is_image_tx(tx):
                    image_packages.add(parse_nevra(pkg["nevra"]).name)
                if pkg["action"] == "Install" and pkg["reason"] == Reason.USER:
                    installs.setdefault(parse_nevra(pkg["nevra"]).name, set()).add(tx["id"])
        return UserHistory(
            image_tx_ids=frozenset(int(t["id"]) for t in txs if _is_image_tx(t)),
            installs={n: frozenset(s) for n, s in installs.items()},
            image_packages=frozenset(image_packages),
        )

    def installed_key_fingerprints(self) -> frozenset[str]:
        return frozenset(fp for fp, _ in parse_rpmkeys(self._out(["rpmkeys", "--list"])))

    def leaves(self) -> frozenset[str]:
        """Pacotes folha do grafo de dependências."""
        return parse_leaves(self._out([*DNF, "leaves"]))

    def _owners(self, files: list[str]) -> list[str]:
        """Pacote dono de cada .repo (uma linha por arquivo, na mesma ordem)."""
        # `rpm -qf` sai com o número de arquivos sem dono: 0 só quando todos têm dono
        res = self.run(["rpm", "-qf", *[f"{REPO_DIR}/{f}" for f in files]])
        lines = res.stdout.splitlines()
        unowned = sum(1 for ln in lines if ln.startswith("file ") and "not owned" in ln)
        if res.returncode not in (0, unowned):
            raise RuntimeError(f"rpm -qf terminou com código inesperado {res.returncode}")
        return lines

    def _releasever(self) -> str:
        """Versão do Fedora (ex.: "44"), tirada da transação mais recente do histórico."""
        listing = json.loads(self._out([*DNF, "history", "list", "--json"]))
        return str(listing[0]["releasever"]) if listing else ""

    def repos(self, needed: set[str]) -> list[RepoSpec]:
        """RepoSpec dos repositórios `needed`, lendo todos os .repo e seus donos.

        1) lista os .repo e descobre o dono de cada um; 2) separa as seções por id e guarda só as
        dos repos necessários; 3) se o id aparece em mais de um arquivo, escolhe um; 4) monta o
        RepoSpec (com fingerprints extras para COPR).
        """
        files = [f for f in self._out(["ls", "-1", REPO_DIR]).split() if f.endswith(".repo")]
        owners = self._owners(files)
        if len(owners) != len(files):
            raise ValueError("rpm -qf devolveu número de linhas diferente do de arquivos")
        releasever = self._releasever()
        cands: dict[str, list[tuple[str | None, dict[str, str]]]] = {}
        for fname, owner_line in zip(files, owners, strict=True):
            # "file ... is not owned by any package" = .repo sem dono
            owner = None if owner_line.startswith("file ") else _name_of_nvra(owner_line)
            for sid, opts in parse_repo_file(self._out(["cat", f"{REPO_DIR}/{fname}"])).items():
                if sid not in needed:
                    continue
                cands.setdefault(sid, []).append((owner, opts))
        keys = parse_rpmkeys(self._out(["rpmkeys", "--list"])) if cands else []
        specs = []
        for sid, options in sorted(cands.items()):
            # duplicado: prefere o que tem gpgkey remota (file:// local não é portável);
            # sort estável mantém a ordem dos arquivos como desempate
            options.sort(key=lambda c: _local_key_only(c[1]))
            owner, opts = options[0]
            spec = self._spec(sid, owner, opts, releasever, len(options))
            if spec.kind is RepoKind.COPR:
                fps = sorted({*spec.gpgkeys, *_copr_fingerprints(spec.owner, keys)})
                spec = replace(spec, gpgkeys=tuple(fps))
            specs.append(spec)
        return specs

    @staticmethod
    def _spec(
        sid: str, owner: str | None, opts: dict[str, str], releasever: str, n: int
    ) -> RepoSpec:
        # Aqui se decide o `kind` do repo (pelo dono do arquivo) e quais avisos ele recebe.
        warnings: list[str] = []
        if n > 1:
            warnings.append(f"duplicate_repo_id: definido em {n} arquivos; usado o primeiro")
        package = None
        owner_repo: str | None = None
        if owner is not None and owner.startswith("fedora-repos"):
            kind = RepoKind.OFFICIAL
        elif sid.startswith("copr:"):
            kind = RepoKind.COPR
            owner_repo = "/".join(sid.split(":", 3)[2:]).replace(":", "/", 1)
        elif owner == "fedora-workstation-repositories":
            kind, package = RepoKind.THIRD_PARTY, owner
        elif owner is not None and owner.endswith("-release"):
            kind, package = RepoKind.RELEASE_PACKAGE, owner
        else:
            kind = RepoKind.REPOFILE
            if owner is not None:
                package = owner
                warnings.append(f"owned_by_package: arquivo .repo pertence a {owner}")
        # só a 1ª baseurl; as chaves e URLs são varridas à procura de credenciais embutidas
        baseurl = opts.get("baseurl", "").split()[0] if opts.get("baseurl", "").strip() else None
        keys = tuple(opts.get("gpgkey", "").split())
        urls = [u for u in (baseurl, *keys, opts.get("metalink"), opts.get("mirrorlist")) if u]
        if any(_has_credentials(u) for u in urls):
            warnings.append("credentials_in_url: URL contém credenciais")
        if kind is RepoKind.COPR:
            keys = tuple(k for k in keys if not k.startswith("file://"))  # `copr enable` cuida
        elif package is None and keys and all(k.startswith("file://") for k in keys):
            warnings.append("local_gpgkey: chave só existe nesta máquina (file://), sem URL remota")
        if opts.get("gpgcheck", "1").strip() == "0":
            warnings.append("gpgcheck_disabled: gpgcheck=0")
        return RepoSpec(
            id=sid,
            kind=kind,
            name=opts.get("name", sid),
            baseurl=normalize_url(baseurl, releasever) if baseurl and releasever else baseurl,
            gpgkey_urls=tuple(sorted(keys)),
            gpgkeys=tuple(sorted(set(_fingerprints(keys)))),
            owner=owner_repo,
            package=package,
            warnings=tuple(warnings),
        )


def _local_key_only(opts: dict[str, str]) -> bool:
    keys = opts.get("gpgkey", "").split()
    return bool(keys) and all(k.startswith("file://") for k in keys)


def _fingerprints(urls: Iterable[str]) -> list[str]:
    """Fingerprints de 40 hex presentes no nome do arquivo da chave (quando houver)."""
    return [
        m.lower()
        for u in urls
        for m in re.findall(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])", u)
    ]
