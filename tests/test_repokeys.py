import shutil
from pathlib import Path

import pytest

from distro_recovery.keys import fetch_key, key_fingerprints
from distro_recovery.model import RepoKind, RepoSpec
from distro_recovery.repokeys import KNOWN_GPGKEYS, resolve_gpgkeys, verify_keys

LOCAL = "local_gpgkey: chave só existe nesta máquina (file://), sem URL remota"
MS = "https://packages.microsoft.com/keys/microsoft.asc"
KEYS = Path(__file__).parent / "fixtures" / "keys"


def spec(
    id_="code", urls=("file:///etc/pki/rpm-gpg/x.gpg",), warnings=(LOCAL,), kind=RepoKind.REPOFILE
):
    return RepoSpec(id=id_, kind=kind, name=id_, gpgkey_urls=urls, warnings=warnings)


def test_known_table_has_vscode_and_vscodium():
    assert KNOWN_GPGKEYS["code"] == MS
    assert KNOWN_GPGKEYS["gitlab.com_paulcarroty_vscodium_repo"].startswith("https://gitlab.com/")
    assert all(u.startswith("https://") for u in KNOWN_GPGKEYS.values())


def test_known_vendor_replaces_local_key_and_clears_blocking_warning():
    [out] = resolve_gpgkeys([spec()], {})
    assert out.gpgkey_urls == (MS,)
    assert not any(w.startswith("local_gpgkey") for w in out.warnings)
    assert any(w.startswith("gpgkey_source") for w in out.warnings)


def test_user_override_beats_table():
    [out] = resolve_gpgkeys([spec()], {"code": "https://example.org/k.asc"})
    assert out.gpgkey_urls == ("https://example.org/k.asc",)


def test_override_also_applies_to_repos_with_a_remote_key():
    s = spec("vivaldi", ("https://old/key",), ())
    [out] = resolve_gpgkeys([s], {"vivaldi": "https://new/key"})
    assert out.gpgkey_urls == ("https://new/key",)


def test_unknown_repo_without_remote_key_stays_blocked():
    [out] = resolve_gpgkeys([spec("meu-repo")], {})
    assert out.gpgkey_urls == ("file:///etc/pki/rpm-gpg/x.gpg",)
    assert any(w.startswith("local_gpgkey") for w in out.warnings)


def test_repo_with_remote_key_is_untouched():
    s = spec("code", ("https://elsewhere/key",), ())
    assert resolve_gpgkeys([s], {}) == [s]


def test_override_must_be_https():
    with pytest.raises(ValueError, match="https"):
        resolve_gpgkeys([spec()], {"code": "http://insecure/k"})


FP = "aa" * 20


def test_verify_ok_when_fetched_key_is_installed():
    s = spec("code", (MS,), ())
    assert verify_keys([s], {FP}, lambda u: b"k", lambda b: [FP]) == {}


def test_verify_ok_when_any_key_in_file_is_installed():
    s = spec("vivaldi", ("https://v/k",), ())
    assert verify_keys([s], {FP}, lambda u: b"k", lambda b: ["bb" * 20, FP]) == {}


def test_verify_mismatch_warns():
    s = spec("code", (MS,), ())
    out = verify_keys([s], {FP}, lambda u: b"k", lambda b: ["cc" * 20])
    assert out["code"][0].startswith("gpgkey_mismatch")


def test_verify_unreachable_warns_and_does_not_raise():
    def boom(url):
        raise OSError("sem rede")

    out = verify_keys([spec("code", (MS,), ())], {FP}, boom, lambda b: [FP])
    assert out["code"][0].startswith("gpgkey_unreachable")


def test_verify_skips_local_keys():
    s = spec("code", ("file:///x",), ())
    assert verify_keys([s], {FP}, lambda u: 1 / 0, lambda b: [FP]) == {}


def test_fetch_key_refuses_non_https():
    with pytest.raises(ValueError, match="https"):
        fetch_key("http://example.org/key")
    with pytest.raises(ValueError, match="https"):
        fetch_key("file:///etc/passwd")


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg não instalado")
def test_key_fingerprints_reads_armored_key_without_touching_keyrings():
    fps = key_fingerprints((KEYS / "microsoft.asc").read_bytes())
    assert "bc528686b50d79e339d3721ceb3e94adbe1229cf" in fps
