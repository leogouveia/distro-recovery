#!/usr/bin/env python3
"""Mostra a saída de um comando numa caixa de altura fixa que rola no mesmo lugar.

Lê a entrada padrão. Em um terminal, desenha uma caixa de N linhas e a redesenha sem
acrescentar linhas à tela. Fora de um terminal (arquivo, pipe), imprime as linhas da caixa
com o prefixo "  | ", para o log continuar legível.

  --all        todas as linhas vão para a caixa
  (padrão)     só as linhas que começam com "@@ " vão para a caixa; as demais são
               impressas normalmente, acima dela (ex.: PASS/FAIL, que não devem sumir)
"""

from __future__ import annotations

import argparse
import os
import re
import select
import shutil
import sys
import time
from collections import deque
from typing import TextIO

MARKER = "@@ "
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_DIM, _RESET = "\x1b[2m", "\x1b[0m"


def clean(line: str) -> str:
    """Tira cores, retorno de carro (barras de progresso) e caracteres de controle."""
    segments = [s for s in line.rstrip("\n").split("\r") if s.strip()]
    text = _ANSI.sub("", segments[-1] if segments else "")
    return "".join(ch for ch in text if ch == "\t" or ch.isprintable()).expandtabs(4)


class Box:
    def __init__(
        self,
        out: TextIO,
        *,
        lines: int = 8,
        title: str = "",
        tty: bool = True,
        columns: int | None = None,
    ) -> None:
        self.out, self.lines, self.title, self.tty = out, lines, title, tty
        self.columns = columns
        self.buffer: deque[str] = deque(maxlen=lines)
        self.total = 0
        self.drawn = False
        self.dirty = False

    # ------------------------------------------------------------ entrada
    def add(self, line: str) -> None:
        self.total += 1
        text = clean(line)
        if not self.tty:
            self.out.write(f"  | {text}\n")
            self.out.flush()
            return
        self.buffer.append(text)
        self.dirty = True

    def passthrough(self, line: str) -> None:
        text = line.rstrip("\n")
        if not self.tty:
            self.out.write(text + "\n")
            self.out.flush()
            return
        self._erase()
        self.out.write(text + "\n")
        self.dirty = True

    def flush(self) -> None:
        if self.tty and self.dirty:
            self._draw()
        self.out.flush()

    def close(self) -> None:
        if self.tty:
            self._erase()
            if self.total:
                label = f"{self.title}: " if self.title else ""
                self.out.write(f"{_DIM}  └ {label}{self.total} linhas de saída{_RESET}\n")
        self.out.flush()

    # ------------------------------------------------------------ desenho
    def _width(self) -> int:
        cols = self.columns or shutil.get_terminal_size((80, 24)).columns
        return max(20, cols - 1)  # evita quebra automática na última coluna

    def _frame(self) -> list[str]:
        width, inner = self._width(), self._width() - 4
        rows = list(self.buffer) + [""] * (self.lines - len(self.buffer))
        head = f"─ {self.title} " if self.title else "─"
        top = "┌" + head.ljust(width - 2, "─")[: width - 2] + "┐"
        body = [f"│ {r[:inner].ljust(inner)} │" for r in rows]
        return [top, *body, "└" + "─" * (width - 2) + "┘"]

    def _draw(self) -> None:
        if self.drawn:
            self.out.write(f"\x1b[{self.lines + 2}A\r")
        for row in self._frame():
            self.out.write(f"{_DIM}{row}{_RESET}\x1b[K\n")
        self.drawn, self.dirty = True, False

    def _erase(self) -> None:
        if self.drawn:
            self.out.write(f"\x1b[{self.lines + 2}A\r\x1b[J")
            self.drawn = False


def _pump(box: Box, all_lines: bool) -> None:
    """Lê stdin sem bloquear o redesenho: atualiza a caixa a cada ~50 ms."""
    fd = sys.stdin.fileno()
    pending = b""
    last = 0.0
    eof = False
    while not eof:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                eof = True
            pending += chunk
        *complete, pending = pending.split(b"\n")
        if eof and pending:
            complete.append(pending)
            pending = b""
        for raw in complete:
            line = raw.decode("utf-8", errors="replace")
            if all_lines:
                box.add(line)
            elif line.startswith(MARKER):
                box.add(line[len(MARKER) :])
            else:
                box.passthrough(line)
        now = time.monotonic()
        if eof or now - last >= 0.05:
            box.flush()
            last = now


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--lines", type=int, default=8, help="altura da caixa (padrão: 8)")
    parser.add_argument("--title", default="", help="título mostrado na borda")
    parser.add_argument("--all", action="store_true", help="todas as linhas vão para a caixa")
    args = parser.parse_args()
    if args.lines < 1:
        parser.error("--lines precisa ser pelo menos 1")

    tty = sys.stdout.isatty()
    lines = args.lines
    if tty:  # a caixa precisa caber na tela
        lines = max(1, min(lines, shutil.get_terminal_size((80, 24)).lines - 4))
    box = Box(sys.stdout, lines=lines, title=args.title, tty=tty)
    try:
        _pump(box, args.all)
    except KeyboardInterrupt:
        pass
    finally:
        box.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
