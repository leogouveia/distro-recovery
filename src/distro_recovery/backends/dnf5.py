"""Backend Fedora / DNF5. Toda leitura passa pelo runner injetado."""

from __future__ import annotations

import configparser
import json
import re
from collections.abc import Iterable

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


_KEY = re.compile(r"^(\S.*?)\s+:\s?(.*)$")
_CONT = re.compile(r"^\s+:\s?(.*)$")


def parse_info_blocks(text: str) -> list[dict[str, list[str]]]:
    """`group info` / `environment info`: blocos `Chave : valor` com continuações `   : valor`."""
    blocks: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    key = ""
    for line in text.splitlines():
        if m := _CONT.match(line):
            if current is not None and key:
                current[key].append(m.group(1).strip())
        elif m := _KEY.match(line):
            key = m.group(1).strip()
            if key == "Id" or current is None:
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
    names = set()
    for line in text.splitlines():
        # `- nevra` por leaf; membros de ciclos de dependência vêm só indentados
        if line.startswith("- ") or line.startswith("  "):
            names.add(parse_nevra(line.strip().removeprefix("- ")).name)
    return frozenset(names)


def _is_yes(value: str) -> bool:
    return value.strip().lower() in ("yes", "true")


def _name_of_nvra(nvra: str) -> str:
    m = re.match(r"^(.+)-[^-]+-[^-]+\.[^.-]+$", nvra.strip())
    if m is None:
        raise ValueError(f"nvra inválido: {nvra!r}")
    return m.group(1)


def _packages_of(info: dict[str, list[str]]) -> frozenset[str]:
    return frozenset(info.get("Mandatory packages", []) + info.get("Default packages", []))


def normalize_url(url: str, releasever: str) -> str:
    """Versões fixas -> $releasever, arquitetura -> $basearch."""
    arch = "|".join(re.escape(a) for a in ARCHES)
    url = re.sub(rf"(?<![A-Za-z0-9_.])({arch})(?![A-Za-z0-9_.])", "$basearch", url)
    return re.sub(rf"(?<![\d.]){re.escape(releasever)}(?!\d|\.\d)", "$releasever", url)


def _has_credentials(url: str) -> bool:
    return re.search(r"://[^/\s@]+@", url) is not None


def parse_repo_file(text: str) -> dict[str, dict[str, str]]:
    cp = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read_string(text)
    return {s: dict(cp.items(s)) for s in cp.sections()}


# ---------------------------------------------------------------- backend


class Dnf5Backend:
    family = "fedora"

    def __init__(self, runner: Runner) -> None:
        self.run = runner

    def _out(self, argv: list[str]) -> str:
        res: CmdResult = self.run(argv)
        if res.returncode != 0:
            raise RuntimeError(f"{' '.join(argv[:4])}… terminou com código {res.returncode}")
        return res.stdout

    def detect(self) -> bool:
        res = self.run(["rpm", "-q", "--whatprovides", "system-release"])
        return res.returncode == 0 and res.stdout.startswith("fedora-release")

    def installed(self) -> list[InstalledPkg]:
        return parse_installed(
            self._out([*DNF, "repoquery", "--installed", "--queryformat", QUERYFORMAT])
        )

    def _group(self, group_id: str) -> Group:
        blocks = parse_info_blocks(self._out([*DNF, "group", "info", group_id]))
        info = blocks[0]
        return Group(group_id, info.get("Name", [group_id])[0], _packages_of(info))

    def installed_groups(self) -> list[Group]:
        ids = parse_list_ids(self._out([*DNF, "group", "list", "--installed"]))
        return [self._group(i) for i in sorted(ids)]

    def _protected(self) -> frozenset[str]:
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
        listing = json.loads(self._out([*DNF, "history", "list", "--json"]))
        ids = [str(h["id"]) for h in listing]
        if not ids:
            return UserHistory(first_tx_id=0, installs={})
        txs = json.loads(self._out([*DNF, "history", "info", *ids, "--json"]))
        installs: dict[str, set[int]] = {}
        for tx in txs:
            if tx.get("status") != "Ok":
                continue
            for pkg in tx.get("packages", []):
                if pkg["action"] == "Install" and pkg["reason"] == Reason.USER:
                    installs.setdefault(parse_nevra(pkg["nevra"]).name, set()).add(tx["id"])
        return UserHistory(
            first_tx_id=min(int(i) for i in ids),
            installs={n: frozenset(s) for n, s in installs.items()},
        )

    def leaves(self) -> frozenset[str]:
        return parse_leaves(self._out([*DNF, "leaves"]))

    def _owners(self, files: list[str]) -> list[str]:
        # `rpm -qf` sai com o número de arquivos sem dono: 0 só quando todos têm dono
        res = self.run(["rpm", "-qf", *[f"{REPO_DIR}/{f}" for f in files]])
        lines = res.stdout.splitlines()
        unowned = sum(1 for ln in lines if ln.startswith("file ") and "not owned" in ln)
        if res.returncode not in (0, unowned):
            raise RuntimeError(f"rpm -qf terminou com código inesperado {res.returncode}")
        return lines

    def _releasever(self) -> str:
        listing = json.loads(self._out([*DNF, "history", "list", "--json"]))
        return str(listing[0]["releasever"]) if listing else ""

    def repos(self, needed: set[str]) -> list[RepoSpec]:
        files = [f for f in self._out(["ls", "-1", REPO_DIR]).split() if f.endswith(".repo")]
        owners = self._owners(files)
        if len(owners) != len(files):
            raise ValueError("rpm -qf devolveu número de linhas diferente do de arquivos")
        releasever = self._releasever()
        cands: dict[str, list[tuple[str | None, dict[str, str]]]] = {}
        for fname, owner_line in zip(files, owners, strict=True):
            owner = None if owner_line.startswith("file ") else _name_of_nvra(owner_line)
            for sid, opts in parse_repo_file(self._out(["cat", f"{REPO_DIR}/{fname}"])).items():
                if sid not in needed:
                    continue
                cands.setdefault(sid, []).append((owner, opts))
        specs = []
        for sid, options in sorted(cands.items()):
            # duplicado: prefere o que tem gpgkey remota (file:// local não é portável);
            # sort estável mantém a ordem dos arquivos como desempate
            options.sort(key=lambda c: _local_key_only(c[1]))
            owner, opts = options[0]
            specs.append(self._spec(sid, owner, opts, releasever, len(options)))
        return specs

    @staticmethod
    def _spec(
        sid: str, owner: str | None, opts: dict[str, str], releasever: str, n: int
    ) -> RepoSpec:
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
        baseurl = opts.get("baseurl", "").split()[0] if opts.get("baseurl", "").strip() else None
        keys = tuple(opts.get("gpgkey", "").split())
        urls = [u for u in (baseurl, *keys, opts.get("metalink"), opts.get("mirrorlist")) if u]
        if any(_has_credentials(u) for u in urls):
            warnings.append("credentials_in_url: URL contém credenciais")
        if package is None and keys and all(k.startswith("file://") for k in keys):
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
