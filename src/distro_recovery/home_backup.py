"""Backup da HOME com GNU tar e zstd, sem alterar a árvore de origem."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# Nomes específicos de caches; build/dist/.git/.venv podem conter dados únicos.
DEFAULT_EXCLUDES = (
    "./.cache",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "./.npm/_cacache",
    "./.npm/_logs",
    "./.cargo/registry/cache",
)


@dataclass(frozen=True)
class BackupPlan:
    source: Path
    destination: Path
    excludes: tuple[str, ...]

    def command(self, output: Path, *, verbose: bool = False) -> list[str]:
        return [
            "tar",
            "--create",
            "--zstd",
            "--format=pax",
            "--acls",
            "--xattrs",
            "--xattrs-include=*",
            "--selinux",
            "--sparse",
            *(["--verbose"] if verbose else []),
            "--file",
            str(output),
            "--directory",
            str(self.source),
            "--wildcards",
            "--no-anchored",
            "--wildcards-match-slash",
            *(f"--exclude={pattern}" for pattern in self.excludes),
            "--",
            ".",
        ]


def plan_backup(
    source: Path,
    destination: Path,
    *,
    config: Path | None = None,
    excludes: tuple[str, ...] = (),
    default_excludes: bool = True,
) -> BackupPlan:
    source = source.expanduser().resolve(strict=True)
    # Resolve só o pai: nunca seguir um symlink no nome final do arquivo.
    destination = destination.expanduser().absolute()
    destination = destination.parent.resolve(strict=True) / destination.name
    if not source.is_dir() or source == Path("/"):
        raise ValueError("a origem deve ser uma pasta HOME, nunca /")
    if not destination.parent.is_dir():
        raise ValueError("a pasta de destino deve existir")
    if destination.is_relative_to(source):
        raise ValueError("o destino deve ficar fora da origem (evita backup recursivo)")
    if not destination.name.endswith(".tar.zst"):
        raise ValueError("o destino deve terminar em .tar.zst")
    if os.path.lexists(destination):
        raise ValueError(f"destino já existe; nunca sobrescrevemos: {destination}")
    patterns = list(DEFAULT_EXCLUDES if default_excludes else ())
    if config is not None:
        try:
            data = YAML(typ="safe").load(config.expanduser().read_text())
        except YAMLError as exc:
            raise ValueError(f"configuração YAML inválida: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {"exclude"}:
            raise ValueError("configuração deve conter apenas a chave exclude (lista de padrões)")
        values = data["exclude"]
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError("exclude deve ser uma lista de strings")
        patterns.extend(values)
    patterns.extend(excludes)
    if any(not p.strip() or "\x00" in p or "\n" in p or "\r" in p for p in patterns):
        raise ValueError("padrões de exclusão não podem ser vazios ou conter NUL/quebras de linha")
    return BackupPlan(source, destination, tuple(dict.fromkeys(patterns)))


def check_tools() -> None:
    for tool in ("tar", "zstd"):
        if shutil.which(tool) is None:
            raise ValueError(f"{tool} não encontrado; instale GNU tar e zstd")
    result = subprocess.run(["tar", "--version"], check=True, capture_output=True, text=True)
    if "GNU tar" not in result.stdout:
        raise ValueError("é necessário GNU tar (ACLs, xattrs e SELinux)")


def _create_archive(plan: BackupPlan, output: Path, *, verbose: bool, env: dict[str, str]) -> bool:
    """Tolera apenas mudanças reconhecidas, mantendo outros diagnósticos como falhas."""
    # Mensagens estáveis para distinguir alterações de erros de leitura/compressão.
    tar_env = {**env, "LC_ALL": "C"}
    changed = False
    unexpected = False
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as diagnostics:
        result = subprocess.run(
            plan.command(output, verbose=verbose), check=False, env=tar_env, stderr=diagnostics
        )
        diagnostics.seek(0)
        for line in diagnostics:
            sys.stderr.write(line)
            message = line.rstrip("\r\n")
            if not message:
                continue
            if message.startswith("tar: ") and (
                message.endswith(": file changed as we read it")
                or re.search(r": File shrank by [0-9]+ bytes; padding with zeros$", message)
            ):
                changed = True
            elif not (message.startswith("tar: ") and message.endswith(": socket ignored")):
                unexpected = True
        if result.returncode != 0 and not (result.returncode == 1 and changed and not unexpected):
            raise subprocess.CalledProcessError(result.returncode, result.args)
    return changed


def create_backup(
    plan: BackupPlan, *, verbose: bool = False, report: Callable[[str], None] | None = None
) -> bool:
    """Publica sem sobrescrever após verificar; retorna se houve arquivos alterados."""
    env = os.environ.copy()
    # Opções externas não podem alterar seleção, compressor ou metadados.
    for key in ("TAR_OPTIONS", "ZSTD_CLEVEL", "ZSTD_NBTHREADS"):
        env.pop(key, None)
    fd, name = tempfile.mkstemp(
        prefix=".backup-home-", suffix=".partial", dir=plan.destination.parent
    )
    temporary = Path(name)
    stage = "criação do tar+zstd"

    def announce(message: str) -> None:
        if report is not None:
            report(message)

    try:
        with os.fdopen(fd, "wb") as stream:
            announce("[1/3] Criando tar+zstd" + ("; itens processados:" if verbose else "..."))
            changed = _create_archive(plan, temporary, verbose=verbose, env=env)
            if changed:
                announce(
                    "aviso: arquivos mudaram durante a leitura; continuando o backup. "
                    "Esses itens podem estar inconsistentes; consulte as mensagens do tar acima."
                )
            # Leitura completa: CRC zstd e estrutura tar, sem extrair nada.
            stage = "verificação zstd"
            announce("[2/3] Verificando integridade da compressão zstd...")
            subprocess.run(["zstd", "--test", "--quiet", "--", str(temporary)], check=True, env=env)
            stage = "verificação tar"
            announce("[3/3] Verificando leitura do arquivo tar...")
            subprocess.run(
                ["tar", "--list", "--zstd", "--file", str(temporary)],
                check=True,
                env=env,
                stdout=subprocess.DEVNULL,
            )
            os.fsync(stream.fileno())
        os.link(temporary, plan.destination)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"{stage} falhou (código {exc.returncode}). Nenhum backup foi publicado; "
            "o arquivo parcial foi removido. Consulte as mensagens acima. "
            "Se houver erros de leitura, "
            "precisam ser resolvidos ou os caminhos excluídos explicitamente com --exclude. "
            "Avisos de socket ignorado são normais e não causam essa falha."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return changed
