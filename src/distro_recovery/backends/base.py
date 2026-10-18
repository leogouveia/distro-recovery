"""Protocol Backend e runners de comando (reais e de fixtures)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ruamel.yaml import YAML

from distro_recovery.model import Baseline, Group, InstalledPkg, RepoSpec, UserHistory


@dataclass(frozen=True)
class CmdResult:
    stdout: str
    returncode: int = 0


class Runner(Protocol):
    def __call__(self, argv: Sequence[str]) -> CmdResult: ...


class CommandNotInFixtures(KeyError):
    pass


class ReadOnlyViolation(RuntimeError):
    pass


class FixtureRunner:
    """Responde comandos a partir de `manifest.yml` (argv exato -> arquivo)."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        data = YAML(typ="safe").load((self.directory / "manifest.yml").read_text())
        self._index = {tuple(c["argv"]): c for c in data["commands"]}

    def __call__(self, argv: Sequence[str]) -> CmdResult:
        entry = self._index.get(tuple(argv))
        if entry is None:
            raise CommandNotInFixtures(f"comando sem fixture: {list(argv)!r}")
        text = (self.directory / entry["file"]).read_text()
        return CmdResult(text, int(entry.get("returncode", 0)))


_DNF_READ = {
    "repoquery": None,
    "leaves": None,
    "group": {"list", "info"},
    "environment": {"list", "info"},
    "history": {"list", "info"},
}


def _check_read_only(argv: Sequence[str]) -> None:
    if not argv:
        raise ReadOnlyViolation("comando vazio")
    prog, rest = argv[0], list(argv[1:])
    if prog in {"ls", "cat"}:
        return
    if prog == "rpmkeys" and rest[:1] in (["--list"], ["-l"]):
        return
    if prog == "rpm" and rest and (rest[0].startswith("-q") or rest[0] == "--query"):
        return
    if prog == "dnf5":
        words = [a for a in rest if not a.startswith("-")]
        if words and words[0] in _DNF_READ:
            allowed = _DNF_READ[words[0]]
            if allowed is None or (len(words) > 1 and words[1] in allowed):
                return
    raise ReadOnlyViolation(f"comando não permitido (somente leitura): {list(argv)!r}")


class SubprocessRunner:
    """Executa comandos reais, só os de leitura. Nunca usa shell nem stdin."""

    def __init__(self, timeout: float = 300) -> None:
        self.timeout = timeout

    def __call__(self, argv: Sequence[str]) -> CmdResult:
        _check_read_only(argv)
        proc = subprocess.run(  # noqa: S603
            list(argv),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=self.timeout,
            check=False,
        )
        return CmdResult(proc.stdout, proc.returncode)


class Backend(Protocol):
    family: str

    def detect(self) -> bool: ...
    def installed(self) -> list[InstalledPkg]: ...
    def installed_groups(self) -> list[Group]: ...
    def baseline(self) -> Baseline: ...
    def history_user_installs(self) -> UserHistory: ...
    def leaves(self) -> frozenset[str]: ...
    def repos(self, needed: set[str]) -> list[RepoSpec]: ...
