"""Endereços das chaves GPG dos repositórios: tabela de fornecedores e conferência."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from distro_recovery.model import RepoSpec

# Usada só quando o .repo local perdeu o endereço da chave (só `file://`).
# O usuário pode sobrescrever qualquer entrada em decisions.yml (repos.gpgkey).
KNOWN_GPGKEYS: dict[str, str] = {
    "code": "https://packages.microsoft.com/keys/microsoft.asc",
    "gitlab.com_paulcarroty_vscodium_repo": (
        "https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/-/raw/master/pub.gpg"
    ),
}


def _is_local_key_warning(w: str) -> bool:
    return w.startswith("local_gpgkey")


def resolve_gpgkeys(
    specs: Iterable[RepoSpec], overrides: Mapping[str, str], known: Mapping[str, str] | None = None
) -> list[RepoSpec]:
    """Preenche o endereço da chave de repos que o perderam.

    Prioridade: `repos.gpgkey` do decisions.yml > tabela KNOWN_GPGKEYS. Ao resolver, o aviso
    `local_gpgkey` (que mandaria o repo para pending) é trocado por um aviso informativo.
    """
    known = KNOWN_GPGKEYS if known is None else known
    for repo_id, url in overrides.items():
        if not url.startswith("https://"):
            raise ValueError(f"repos.gpgkey.{repo_id}: só https é aceito ({url!r})")
    out = []
    for s in specs:
        url, source = None, ""
        if s.id in overrides:
            url, source = overrides[s.id], "decisions.yml"
        elif any(_is_local_key_warning(w) for w in s.warnings) and s.id in known:
            url, source = known[s.id], "tabela de fornecedores conhecidos"
        if url is None:  # nada a corrigir neste repositório
            out.append(s)
            continue
        warnings = [w for w in s.warnings if not _is_local_key_warning(w)]
        warnings.append(f"gpgkey_source: endereço da chave vem de {source}")
        out.append(replace(s, gpgkey_urls=(url,), warnings=tuple(warnings)))
    return out


def verify_keys(
    specs: Iterable[RepoSpec],
    installed: set[str],
    fetch: Callable[[str], bytes],
    fingerprints: Callable[[bytes], list[str]],
) -> dict[str, list[str]]:
    """Confere, com internet, se os endereços entregam alguma chave já instalada.

    Devolve avisos por repositório; vazio = tudo certo. Nunca importa nada.
    """
    result: dict[str, list[str]] = {}
    for s in specs:
        urls = [u for u in s.gpgkey_urls if u.startswith("https://")]
        if not urls:
            continue
        fetched: set[str] = set()
        problems: list[str] = []
        for u in urls:
            try:
                fetched.update(fingerprints(fetch(u)))
            except (OSError, ValueError) as exc:
                problems.append(f"gpgkey_unreachable: {u} ({exc})")
        if not problems and not fetched & installed:
            problems.append(
                "gpgkey_mismatch: o endereço entrega uma chave diferente das instaladas "
                f"({', '.join(sorted(fetched)) or 'nenhuma'})"
            )
        if problems:
            result[s.id] = problems
    return result
