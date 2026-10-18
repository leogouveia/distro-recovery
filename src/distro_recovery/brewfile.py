"""Leitura do ~/.Brewfile, só para detectar nomes duplicados com pacotes nativos."""

from __future__ import annotations

import re

_BREW = re.compile(r"""^\s*brew\s+["']([^"']+)["']""")


def parse_brewfile(text: str) -> frozenset[str]:
    names = set()
    for line in text.splitlines():
        if m := _BREW.match(line):
            names.add(m.group(1).rsplit("/", 1)[-1])  # user/tap/nome -> nome
    return frozenset(names)
