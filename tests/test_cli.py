import hashlib
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from distro_recovery import cli
from distro_recovery.cli import app

FIX = Path(__file__).parent / "fixtures" / "dnf5"
LOAD = YAML(typ="safe").load


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(cli, "_now", lambda: "2026-10-17T22:00:00Z")


def run(tmp_path, *args):
    return CliRunner().invoke(
        app, ["backup", "--ansible", "--from-fixtures", str(FIX), "--dir", str(tmp_path), *args]
    )


def gen(tmp_path, name):
    return LOAD((tmp_path / "generated" / name).read_text())


def test_backup_from_fixtures_writes_generated_and_reports_pending(tmp_path):
    res = run(tmp_path)
    assert res.exit_code == 2, res.output  # há pendências (REVIEW)
    assert sorted(p.name for p in (tmp_path / "generated").iterdir()) == [
        "fedora.yml",
        "meta.yml",
        "pending.yml",
    ]
    fedora = gen(tmp_path, "fedora.yml")
    assert "pavucontrol" in fedora["packages"] and "vlc" in fedora["packages"]
    assert "7zip" not in fedora["packages"]  # dependência
    assert "chatgpt" not in fedora["packages"]  # local_rpm -> pending
    assert fedora["groups"] == ["development-tools"]  # só grupos fora da baseline
    repo_ids = {r["id"] for r in fedora["repos"]}
    assert {"google-chrome", "vivaldi", "1password"} <= repo_ids
    assert not {"fedora", "updates", "fedora-cisco-openh264"} & repo_ids  # oficiais
    pending = gen(tmp_path, "pending.yml")
    assert {"name": "chatgpt", "reason_code": "local_rpm"} in pending["packages"]
    meta = gen(tmp_path, "meta.yml")
    assert meta["generated_at"] == "2026-10-17T22:00:00Z" and meta["family"] == "fedora"


def test_backup_is_deterministic_across_runs_and_dirs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    run(a)
    run(b)
    for name in ("fedora.yml", "pending.yml", "meta.yml"):
        assert (a / "generated" / name).read_bytes() == (b / "generated" / name).read_bytes()


def test_backup_never_touches_decisions_yml(tmp_path):
    decisions = tmp_path / "decisions.yml"
    decisions.write_text('exclude: ["pavucontrol"]\n')
    before = hashlib.sha256(decisions.read_bytes()).hexdigest()
    run(tmp_path)
    assert hashlib.sha256(decisions.read_bytes()).hexdigest() == before
    assert "pavucontrol" not in gen(tmp_path, "fedora.yml")["packages"]


def test_backup_does_not_create_decisions_yml(tmp_path):
    run(tmp_path)
    assert not (tmp_path / "decisions.yml").exists()


def test_include_pulls_a_dependency_in(tmp_path):
    (tmp_path / "decisions.yml").write_text('include: ["7zip"]\n')
    run(tmp_path)
    assert "7zip" in gen(tmp_path, "fedora.yml")["packages"]


def test_include_and_exclude_conflict_is_an_error(tmp_path):
    (tmp_path / "decisions.yml").write_text('include: ["vlc"]\nexclude: ["vlc"]\n')
    res = run(tmp_path)
    assert res.exit_code == 1 and "vlc" in res.output
    assert not (tmp_path / "generated").exists()


def test_exit_zero_when_nothing_is_pending(tmp_path):
    first = run(tmp_path)
    pending = gen(tmp_path, "pending.yml")
    excl = [p["name"] for p in pending["packages"]]
    repos = [r["id"] for r in pending["repos"]] + pending["missing_repos"]
    lines = ["exclude:", *[f'  - "{n}"' for n in excl], "repos:", "  exclude:"]
    lines += [f'    - "{r}"' for r in repos] or ["    []"]
    (tmp_path / "decisions.yml").write_text("\n".join(lines) + "\n")
    assert first.exit_code == 2
    res = run(tmp_path)
    assert res.exit_code == 0, res.output


def test_brewfile_duplicate_goes_to_pending(tmp_path):
    bf = tmp_path / "Brewfile"
    bf.write_text('brew "htop"\n')
    run(tmp_path, "--brewfile", str(bf))
    assert "htop" not in gen(tmp_path, "fedora.yml")["packages"]
    assert {"name": "htop", "reason_code": "duplicated_in_brew"} in gen(tmp_path, "pending.yml")[
        "packages"
    ]


def test_verbose_lists_excluded_with_reason(tmp_path):
    quiet = run(tmp_path / "q")
    loud = run(tmp_path / "v", "--verbose")
    assert "dependency" not in quiet.output
    assert "7zip" in loud.output and "dependency" in loud.output


def test_from_fixtures_requires_explicit_dir():
    res = CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX)])
    assert res.exit_code == 1 and "--dir" in res.output


def test_missing_fixtures_dir_is_an_error(tmp_path):
    res = CliRunner().invoke(
        app, ["backup", "--from-fixtures", str(tmp_path / "nope"), "--dir", str(tmp_path)]
    )
    assert res.exit_code == 1


@pytest.mark.parametrize("cmd", ["review", "diff", "restore", "doctor"])
def test_other_commands_are_stubs_for_now(cmd):
    res = CliRunner().invoke(app, [cmd])
    assert res.exit_code == 1 and "não implementado" in res.output


def test_vscode_and_vscodium_use_known_vendor_key_urls(tmp_path):
    assert run(tmp_path).exit_code == 2  # só o chatgpt pendente
    repos = {r["id"]: r for r in gen(tmp_path, "fedora.yml")["repos"]}
    assert repos["code"]["gpgkey_urls"] == ["https://packages.microsoft.com/keys/microsoft.asc"]
    assert repos["gitlab.com_paulcarroty_vscodium_repo"]["gpgkey_urls"][0].startswith("https://")
    webapp = repos["copr:copr.fedorainfracloud.org:kylegospo:webapp-manager"]
    assert "gpgkey_urls" not in webapp  # COPR: `copr enable` cuida da chave
    assert gen(tmp_path, "pending.yml")["repos"] == []


def test_decisions_gpgkey_override_is_used(tmp_path):
    (tmp_path / "decisions.yml").write_text(
        'repos:\n  gpgkey:\n    code: "https://example.org/k.asc"\n'
    )
    run(tmp_path)
    repos = {r["id"]: r for r in gen(tmp_path, "fedora.yml")["repos"]}
    assert repos["code"]["gpgkey_urls"] == ["https://example.org/k.asc"]


def test_only_chatgpt_is_pending_now(tmp_path):
    run(tmp_path)
    assert [p["name"] for p in gen(tmp_path, "pending.yml")["packages"]] == ["chatgpt"]


def test_verify_keys_is_off_by_default_and_never_hits_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "fetch_key", lambda url: 1 / 0)
    assert run(tmp_path).exit_code == 2


def test_verify_keys_reports_mismatch_without_changing_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "fetch_key", lambda url: b"k")
    monkeypatch.setattr(cli, "key_fingerprints", lambda b: ["cc" * 20])
    res = run(tmp_path, "--verify-keys")
    assert res.exit_code == 2, res.output
    assert "gpgkey_mismatch" in res.output and "code" in res.output


def test_verify_keys_ok_prints_confirmation(tmp_path, monkeypatch):
    from distro_recovery.backends.dnf5 import parse_rpmkeys

    installed = [fp for fp, _ in parse_rpmkeys((FIX / "rpmkeys.txt").read_text())]
    monkeypatch.setattr(cli, "fetch_key", lambda url: b"k")
    monkeypatch.setattr(cli, "key_fingerprints", lambda b: installed)
    res = run(tmp_path, "--verify-keys")
    assert "gpgkey_mismatch" not in res.output and "gpgkey_unreachable" not in res.output
    assert "chaves conferidas" in res.output
