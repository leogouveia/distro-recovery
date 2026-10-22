from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import typer
from ruamel.yaml import YAML

from distro_recovery.backends.base import CommandNotInFixtures, FixtureRunner, SubprocessRunner
from distro_recovery.backends.dnf5 import Dnf5Backend
from distro_recovery.brewfile import parse_brewfile
from distro_recovery.classify import Context, classify_all, split_repos
from distro_recovery.decisions_template import render_decisions_template
from distro_recovery.home_backup import check_tools, create_backup, plan_backup
from distro_recovery.keys import fetch_key, key_fingerprints
from distro_recovery.merge import build_vars, load_decisions
from distro_recovery.model import Confidence
from distro_recovery.render import (
    render_ansible_vars,
    render_fedora,
    render_meta,
    render_pending,
)
from distro_recovery.repokeys import resolve_gpgkeys, verify_keys

# Camada de orquestração: a CLI só liga as peças (backend -> classify -> render) e faz o I/O.
# A lógica de decisão mora em classify.py/merge.py, que não leem nem escrevem nada.
app = typer.Typer(help="Inventário e restauração de aplicativos instalados.", no_args_is_help=True)

DEFAULT_DIR = Path("~/.config/distro-recovery")
PACKAGED_ANSIBLE = Path(__file__).parent / "ansible"
EXIT_OK, EXIT_ERROR, EXIT_PENDING = 0, 1, 2  # 2 = backup concluído, mas há pendências
_IMAGE_HASH = re.compile(r"^[0-9a-f]{32}$")  # from_repo dos pacotes instalados pela mídia


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(message: str) -> typer.Exit:
    """Imprime o erro no stderr e devolve a exceção de saída (use com `raise _fail(...)`)."""
    typer.echo(f"erro: {message}", err=True)
    return typer.Exit(EXIT_ERROR)


def _needed_repos(from_repos: set[str]) -> set[str]:
    """Repos a recriar: ignora `@System`/`@commandline` e a mídia de instalação (hash)."""
    return {r for r in from_repos if not r.startswith("@") and not _IMAGE_HASH.match(r)}


def _write_playbook(out_dir: Path, fedora_yml: Path) -> Path:
    """Copia o playbook empacotado e escreve `vars.yml` (generated + decisions)."""
    ans = out_dir / "ansible"
    # apaga a cópia anterior para não sobrar role removido numa versão nova da ferramenta
    for stale in ("roles", "site.yml", "requirements.yml"):
        target = ans / stale
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    shutil.copytree(
        PACKAGED_ANSIBLE, ans, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
    )
    generated = YAML(typ="safe").load(fedora_yml.read_text())
    data = build_vars(generated, load_decisions(out_dir / "decisions.yml"))
    (ans / "vars.yml").write_text(render_ansible_vars(data))
    return ans


@app.callback()
def main() -> None:
    """distro-recovery."""


@app.command()
def backup(
    ansible: bool = typer.Option(False, "--ansible", help="Gera os dados para o playbook Ansible."),
    dir_: Path | None = typer.Option(None, "--dir", help="Diretório de dados do usuário."),
    from_fixtures: Path | None = typer.Option(
        None, "--from-fixtures", help="Lê as saídas de comandos de um diretório de fixtures."
    ),
    brewfile: Path | None = typer.Option(None, "--brewfile", help="Brewfile a comparar."),
    verify_keys_: bool = typer.Option(
        False, "--verify-keys", help="Confere, com internet, os endereços das chaves GPG."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Lista também os pacotes excluídos."),
) -> None:
    """Inventaria o sistema e reescreve `generated/`. Nunca escreve em decisions.yml."""
    # Fase 1: coletar tudo (somente leitura). Qualquer falha vira erro limpo (código 1).
    if from_fixtures is not None and dir_ is None:
        raise _fail("--from-fixtures exige --dir explícito (nunca grava no diretório real)")
    out_dir = (dir_ or DEFAULT_DIR).expanduser()
    try:
        runner = FixtureRunner(from_fixtures) if from_fixtures else SubprocessRunner()
        backend = Dnf5Backend(runner)
        if not backend.detect():
            raise _fail("nenhum backend suportado detectado (Fase 1: Fedora + DNF5)")
        decisions = load_decisions(out_dir / "decisions.yml")
        default_brew = Path("~/.Brewfile").expanduser()
        brew_path = brewfile or (default_brew if from_fixtures is None else None)
        brew = (
            parse_brewfile(brew_path.read_text())
            if brew_path and brew_path.exists()
            else frozenset()
        )
        installed = backend.installed()
        baseline = backend.baseline()
        groups = backend.installed_groups()
        ctx = Context(
            include=decisions.include,
            exclude=decisions.exclude,
            baseline=baseline,
            groups=groups,
            history=backend.history_user_installs(),
            leaves=backend.leaves(),
            brew=brew,
        )
        results = classify_all(installed, ctx)
        specs = resolve_gpgkeys(
            backend.repos(_needed_repos({p.from_repo for p in installed})), decisions.repos_gpgkey
        )
        key_warnings = (
            verify_keys(
                specs, set(backend.installed_key_fingerprints()), fetch_key, key_fingerprints
            )
            if verify_keys_
            else None
        )
    except typer.Exit:
        raise
    except (OSError, ValueError, RuntimeError, CommandNotInFixtures) as exc:
        raise _fail(str(exc)) from exc

    # Fase 2: separar o resultado nos três destinos (fedora.yml / pending.yml / log).
    high = [r.name for r in results if r.confidence is Confidence.HIGH]
    review = [r for r in results if r.confidence is Confidence.REVIEW]
    new_groups = [g.id for g in groups if g.id not in baseline.groups]
    ok_repos, pending_repos = split_repos(specs)
    ok_repos = [r for r in ok_repos if r.id not in decisions.repos_exclude]
    pending_repos = [r for r in pending_repos if r.id not in decisions.repos_exclude]
    found = {s.id for s in specs}
    missing = _needed_repos({p.from_repo for p in installed}) - found - decisions.repos_exclude

    # Fase 3: gravar generated/ (reescrito por inteiro a cada execução).
    gen = out_dir / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    counts = {
        "packages": len(high),
        "groups": len(new_groups),
        "repos": len(ok_repos),
        "pending_packages": len(review),
        "pending_repos": len(pending_repos) + len(missing),
    }
    (gen / "fedora.yml").write_text(render_fedora(high, new_groups, ok_repos))
    (gen / "pending.yml").write_text(render_pending(review, pending_repos, missing))
    (gen / "meta.yml").write_text(
        render_meta(
            family=backend.family,
            tool_version=version("distro-recovery"),
            generated_at=_now(),
            counts=counts,
        )
    )

    # Fase 4: relatório no terminal e código de saída.
    typer.echo(
        f"backup: {counts['packages']} pacotes, {counts['groups']} grupos, "
        f"{counts['repos']} repositórios -> {gen}"
    )
    if ansible:
        try:
            ans = _write_playbook(out_dir, gen / "fedora.yml")
        except (OSError, ValueError) as exc:
            raise _fail(str(exc)) from exc
        typer.echo(f"playbook Ansible atualizado -> {ans}")
    if key_warnings is not None:
        for repo_id, ws in sorted(key_warnings.items()):
            for w in ws:
                typer.echo(f"  aviso: {repo_id}: {w}")
        checked = len([s for s in specs if any(u.startswith("https://") for u in s.gpgkey_urls)])
        typer.echo(f"chaves conferidas: {checked - len(key_warnings)} de {checked} repositórios")
    if verbose:
        for r in results:
            if r.confidence is Confidence.EXCLUDED:
                suffix = f" (@{r.group})" if r.group else ""
                typer.echo(f"  excluído: {r.name}  [{r.reason_code.value}]{suffix}")
    decisions_missing = not (out_dir / "decisions.yml").exists()
    if counts["pending_packages"] or counts["pending_repos"]:
        typer.echo(
            f"pendências: {counts['pending_packages']} pacotes, {counts['pending_repos']} "
            f"repositórios -> revise {gen / 'pending.yml'}"
        )
    if decisions_missing:
        typer.echo(
            "dica: crie o decisions.yml (com instruções e exemplos) com `distro-recovery init`"
        )
    if counts["pending_packages"] or counts["pending_repos"]:
        raise typer.Exit(EXIT_PENDING)


def _stub(name: str) -> None:
    """Marcador dos comandos ainda não implementados (review, diff, doctor)."""
    typer.echo(f"erro: `{name}` ainda não implementado (próxima sessão)", err=True)
    raise typer.Exit(EXIT_ERROR)


@app.command()
def review() -> None:
    """Revisa pendências."""
    _stub("review")


@app.command()
def diff() -> None:
    """Compara o sistema com o inventário."""
    _stub("diff")


@app.command()
def init(
    dir_: Path | None = typer.Option(None, "--dir", help="Diretório de dados do usuário."),
) -> None:
    """Cria o decisions.yml comentado (com instruções e exemplos). Nunca sobrescreve."""
    out_dir = (dir_ or DEFAULT_DIR).expanduser()
    target = out_dir / "decisions.yml"
    if target.exists():
        typer.echo(f"{target} já existe: nada foi alterado")
        return
    pending = None
    pending_yml = out_dir / "generated" / "pending.yml"
    try:
        if pending_yml.exists():
            pending = YAML(typ="safe").load(pending_yml.read_text()) or {}
        out_dir.mkdir(parents=True, exist_ok=True)
        with target.open("x") as fh:  # "x": falha em vez de sobrescrever
            fh.write(render_decisions_template(pending))
    except FileExistsError:
        typer.echo(f"{target} já existe: nada foi alterado")
        return
    except (OSError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"criado {target}")
    typer.echo("abra o arquivo, leia as instruções e escreva as suas decisões")


def _run_playbook(argv: list[str]) -> int:
    """Executa o ansible-playbook (separado para os testes poderem substituir)."""
    return subprocess.run(argv, check=False).returncode  # noqa: S603  (herda o terminal: -K)


@app.command()
def restore(
    dir_: Path | None = typer.Option(None, "--dir", help="Diretório de dados do usuário."),
    check: bool = typer.Option(False, "--check", help="Simula, sem alterar o sistema."),
    ask_become_pass: bool = typer.Option(
        True,
        "--ask-become-pass/--no-ask-become-pass",
        help="Pede a senha do sudo (desnecessário como root ou com sudo sem senha).",
    ),
) -> None:
    """Restaura repositórios, grupos e pacotes. Nunca remove, nem faz downgrade."""
    # As variáveis são recalculadas aqui (generated + decisions.yml atual), então editar o
    # decisions.yml vale sem precisar rodar um novo backup.
    out_dir = (dir_ or DEFAULT_DIR).expanduser()
    fedora_yml = out_dir / "generated" / "fedora.yml"
    if not fedora_yml.exists():
        raise _fail(f"{fedora_yml} não existe: rode `distro-recovery backup` primeiro")
    if shutil.which("ansible-playbook") is None:
        raise _fail("ansible-playbook não encontrado; instale o ansible-core")
    try:
        generated = YAML(typ="safe").load(fedora_yml.read_text())
        data = build_vars(generated, load_decisions(out_dir / "decisions.yml"))
        pending = YAML(typ="safe").load((out_dir / "generated" / "pending.yml").read_text())
    except FileNotFoundError:
        pending = {}
    except (OSError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    if any(pending.get(k) for k in ("packages", "repos", "missing_repos")):
        typer.echo(
            f"aviso: há itens em {out_dir / 'generated' / 'pending.yml'} que NÃO serão restaurados"
        )
    # vars.yml temporário: nada é gravado no diretório do usuário durante o restore
    with tempfile.TemporaryDirectory() as tmp:
        vars_file = Path(tmp) / "vars.yml"
        vars_file.write_text(render_ansible_vars(data))
        argv = ["ansible-playbook", "-i", "localhost,", "-c", "local", "-e", f"@{vars_file}"]
        if ask_become_pass and os.geteuid() != 0:  # -K: o ansible pede a senha do sudo
            argv.append("-K")
        if check:
            argv.append("--check")
        argv.append(str(PACKAGED_ANSIBLE / "site.yml"))
        code = _run_playbook(argv)
    if code != 0:
        raise _fail(f"ansible-playbook terminou com código {code}")


@app.command()
def doctor() -> None:
    """Diagnostica o ambiente."""
    _stub("doctor")


@app.command("backup-home")
def backup_home(
    destination: Path = typer.Option(
        ..., "--destination", "-o", help="Arquivo .tar.zst fora da origem."
    ),
    source: Path | None = typer.Option(None, "--source", help="Origem (padrão: sua HOME)."),
    config: Path | None = typer.Option(None, "--config", help="YAML com lista exclude adicional."),
    exclude: list[str] | None = typer.Option(
        None, "--exclude", help="Padrão tar adicional; repetível."
    ),
    no_default_excludes: bool = typer.Option(
        False, "--no-default-excludes", help="Inclui os caches."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Lista os itens processados pelo tar."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o plano, sem criar arquivos."),
) -> None:
    """Arquiva a HOME com tar+zstd e verifica a integridade antes de publicar."""
    try:
        plan = plan_backup(
            source if source is not None else Path.home(),
            destination,
            config=config,
            excludes=tuple(exclude or ()),
            default_excludes=not no_default_excludes,
        )
        check_tools()
        typer.echo(f"origem: {plan.source}\ndestino: {plan.destination}")
        typer.echo("exclusões: " + (", ".join(plan.excludes) or "nenhuma"))
        if dry_run:
            typer.echo("dry-run: somente plano; não percorre nem estima o conteúdo da HOME")
            typer.echo(shlex.join(plan.command(plan.destination, verbose=verbose)))
            return
        typer.echo("criando e verificando o backup (feche aplicativos que gravam na HOME)...")
        if not verbose:
            typer.echo("dica: use --verbose para acompanhar os itens processados")
        changed = create_backup(plan, verbose=verbose, report=typer.echo)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise _fail(str(exc)) from exc
    if changed:
        typer.echo(
            f"backup da HOME concluído com avisos (integridade do arquivo verificada) "
            f"-> {plan.destination}"
        )
    else:
        typer.echo(f"backup da HOME verificado -> {plan.destination}")
