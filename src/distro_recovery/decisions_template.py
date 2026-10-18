"""Modelo comentado do decisions.yml (criado por `distro-recovery init`). Puro, sem I/O."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EXAMPLE_YAML = """\
include:
  - "meu-app"
exclude:
  - "chatgpt"
repos:
  exclude:
    - "vivaldi"
  gpgkey:
    code: "https://packages.microsoft.com/keys/microsoft.asc"
swaps:
  - from: "ffmpeg-free"
    to: "ffmpeg"
"""

_REASONS = {
    "local_rpm": "instalado de um arquivo .rpm baixado (não vem de nenhum repositório)",
    "user_unconfirmed": "não deu para confirmar se foi você que instalou",
    "in_baseline_but_user": "faz parte da instalação padrão, mas você o instalou de novo depois",
    "duplicated_in_brew": "o mesmo nome está no seu Brewfile",
}

_INTRO = """\
decisions.yml: as SUAS decisões. Só você edita este arquivo.
O distro-recovery nunca altera este arquivo depois de criado; o que a ferramenta escreve
fica em generated/ (e é reescrito a cada backup).

COMO USAR
  1. Rode:  distro-recovery backup
     Se terminar com código 2, há itens que precisam da sua decisão.
  2. Abra generated/pending.yml. Cada item é um app (ou repositório) que a ferramenta não
     soube classificar sozinha, com o motivo (reason_code).
  3. Decida item a item e escreva a decisão neste arquivo (veja abaixo).
  4. Rode o backup de novo: o item decidido sai do pending.yml.

O QUE CADA SEÇÃO FAZ
  include   Apps que você QUER no restore, mesmo que o backup não os tenha detectado.
  exclude   Apps que você NÃO quer no restore.
  repos.exclude  Repositórios que você não quer recriar no restore.
  repos.gpgkey   Endereço (https) da chave de assinatura de um repositório, quando o
                 arquivo .repo não tem um. Sobrescreve a tabela interna.
  swaps     Trocas de pacote (única hipótese em que o restore remove algo): troca "from"
            por "to" em uma transação, só se "from" estiver instalado e "to" não.

REGRAS
  - exclude vence include, e include vence o que foi gerado.
  - O mesmo nome em include e exclude ao mesmo tempo é um erro.
  - Cada chave (include, exclude, repos, swaps) só pode aparecer UMA vez no arquivo.
  - Para ativar um exemplo, copie as linhas sem o "# " e ajuste os nomes.

EXEMPLO COMPLETO (tudo é opcional)
"""

_CLOSING = """\

Escreva as suas decisões abaixo desta linha (sem "# " no começo).
"""


def _comment(text: str) -> list[str]:
    return [f"# {ln}" if ln else "#" for ln in text.splitlines()]


def _suggestions(pending: Mapping[str, Any]) -> list[str]:
    lines = ["#", "# PENDÊNCIAS DO ÚLTIMO BACKUP"]
    packages = pending.get("packages") or []
    repos = pending.get("repos") or []
    missing = pending.get("missing_repos") or []
    if not (packages or repos or missing):
        return [*lines, "# (nenhuma pendência)"]
    if packages:
        lines += [
            "# Apps: copie a linha para 'exclude' (não quero) ou 'include' (quero).",
        ]
        for p in sorted(packages, key=lambda p: p["name"]):
            why = _REASONS.get(p["reason_code"], p["reason_code"])
            lines.append(f'#   - "{p["name"]}"   # {p["reason_code"]}: {why}')
    if repos or missing:
        lines.append("# Repositórios: copie a linha para 'repos.exclude' se não quiser recriá-los.")
        for r in sorted(repos, key=lambda r: r["id"]):
            note = (r.get("warnings") or ["precisa de atenção"])[0]
            lines.append(f'#   - "{r["id"]}"   # {note}')
        for repo_id in sorted(missing):
            lines.append(
                f'#   - "{repo_id}"   # usado por pacotes instalados, mas sem arquivo .repo'
            )
    return lines


def render_decisions_template(pending: Mapping[str, Any] | None) -> str:
    lines = _comment(_INTRO.rstrip("\n"))
    lines += _comment(EXAMPLE_YAML.rstrip("\n"))
    if pending is not None:
        lines += _suggestions(pending)
    lines += _comment(_CLOSING.rstrip("\n"))
    return "\n".join(lines) + "\n"
