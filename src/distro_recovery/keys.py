"""Chaves GPG públicas: download (só https) e leitura de fingerprints. Nada é importado."""

from __future__ import annotations

import subprocess
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse

_MAX_BYTES = 1_000_000


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urlparse(newurl).scheme != "https":
            raise urllib.error.URLError(f"redirecionamento para {newurl!r} recusado: só https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_key(url: str, timeout: float = 30) -> bytes:
    if urlparse(url).scheme != "https":
        raise ValueError(f"só https é aceito para chaves: {url!r}")
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)
    with opener.open(url, timeout=timeout) as resp:  # noqa: S310
        return resp.read(_MAX_BYTES)


def key_fingerprints(armored: bytes) -> list[str]:
    """Fingerprints (minúsculas) das chaves primárias do arquivo, sem tocar em chaveiros."""
    with tempfile.TemporaryDirectory() as home:
        proc = subprocess.run(  # noqa: S603
            ["gpg", "--homedir", home, "--batch", "--show-keys", "--with-colons"],  # noqa: S607
            input=armored,
            capture_output=True,
            check=False,
            timeout=30,
        )
    if proc.returncode != 0:
        raise ValueError(f"gpg não conseguiu ler a chave: {proc.stderr.decode()[:200]}")
    fps, expect = [], False
    for line in proc.stdout.decode().splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            expect = True
        elif fields[0] == "fpr" and expect:
            fps.append(fields[9].lower())
            expect = False
    return fps
