import os
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from ruamel.yaml import YAML

ANSIBLE = Path(str(files("distro_recovery") / "ansible"))
ROLES = ["repos", "swaps", "groups", "packages"]
LOAD = YAML(typ="safe").load


def all_files():
    return [p for p in ANSIBLE.rglob("*") if p.is_file() and p.suffix in {".yml", ".yaml"}]


def role_text(role=None):
    paths = [p for p in all_files() if role is None or f"roles/{role}/" in p.as_posix()]
    return "\n".join(p.read_text() for p in paths)


def test_packaged_playbook_exists():
    assert (ANSIBLE / "site.yml").is_file()
    for r in ROLES:
        assert (ANSIBLE / "roles" / r / "tasks" / "main.yml").is_file(), r


def test_every_yaml_file_parses():
    for p in all_files():
        assert LOAD(p.read_text()) is not None, p


def test_roles_run_in_the_documented_order():
    play = LOAD((ANSIBLE / "site.yml").read_text())[0]
    assert [r if isinstance(r, str) else r["role"] for r in play["roles"]] == ROLES
    assert play["hosts"] == "localhost" and play["connection"] == "local"


def test_uses_the_agreed_modules():
    text = role_text()
    for module in (
        "ansible.builtin.dnf5",
        "ansible.builtin.yum_repository",
        "ansible.builtin.rpm_key",
        "community.general.copr",
    ):
        assert module in text, module


# Princípio 4: o restore nunca remove, nunca faz downgrade e nunca desabilita GPG.
FORBIDDEN = [
    r"state:\s*(absent|removed)",
    r"dnf5?\s+(remove|erase|autoremove)",
    r"nogpgcheck",
    r"disable_gpg_check",
    r"gpgcheck:\s*(false|no|0|\"no\"|\"false\")",
    r"gpgcheck=0",
    r"allow_downgrade:\s*(true|yes)",
    r"validate_certs:\s*(false|no)",
    r"autoremove",
    r"dnf5?\s+downgrade",
    r"\brpm\s+(-e|--erase|-i|-U)\b",
]


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_playbook_never_removes_downgrades_or_disables_gpg(pattern):
    assert re.search(pattern, role_text(), re.IGNORECASE) is None


def test_allowerasing_only_in_swaps_role():
    for r in ROLES:
        has = "--allowerasing" in role_text(r)
        assert has == (r == "swaps"), r


def test_every_dnf5_install_forbids_downgrade():
    for p in all_files():
        text = p.read_text()
        assert text.count("ansible.builtin.dnf5:") == text.count("allow_downgrade: false"), p


def test_repo_definitions_keep_gpgcheck_on():
    assert re.search(r"gpgcheck:\s*true", role_text("repos"))


def _collection_available() -> bool:
    if shutil.which("ansible-doc") is None:
        return False
    proc = subprocess.run(
        ["ansible-doc", "-t", "module", "community.general.copr"],
        capture_output=True,
        check=False,
    )
    # ansible-doc sai com 0 mesmo sem achar o módulo: olhar o cabeçalho da documentação
    return proc.returncode == 0 and b"> MODULE" in proc.stdout


@pytest.mark.skipif(not _collection_available(), reason="ansible-core + community.general ausentes")
def test_syntax_check_passes(tmp_path):
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text(
        'dr_family: "fedora"\ndr_repos: []\ndr_groups: []\ndr_packages: []\ndr_swaps: []\n'
    )
    proc = subprocess.run(
        [
            "ansible-playbook",
            "--syntax-check",
            "-i",
            "localhost,",
            "-e",
            f"@{vars_file}",
            str(ANSIBLE / "site.yml"),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path), "ANSIBLE_HOME": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _run_repos_role(tmp_path, reposdir, repos):
    play = tmp_path / "play.yml"
    play.write_text(
        "- hosts: localhost\n  connection: local\n  gather_facts: false\n  roles: [repos]\n"
    )
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text(
        "dr_family: fedora\ndr_groups: []\ndr_packages: []\ndr_swaps: []\n"
        f"dr_repo_dir: {reposdir}\ndr_repos:\n"
        + "".join(
            f"  - id: {i}\n    kind: repofile\n    name: {i}\n    baseurl: https://example.org/{i}\n"
            for i in repos
        )
    )
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-e", f"@{vars_file}", str(play)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ANSIBLE_ROLES_PATH": str(ANSIBLE / "roles"),
            "ANSIBLE_HOME": str(tmp_path / "home"),
            "ANSIBLE_LOCAL_TEMP": str(tmp_path / "tmp"),
            "ANSIBLE_NOCOLOR": "1",
        },
        check=False,
    )


@pytest.mark.skipif(not _collection_available(), reason="ansible-core + community.general ausentes")
def test_repos_role_never_rewrites_a_repo_that_a_file_already_defines(tmp_path):
    # O pacote do Vivaldi reescreve o próprio vivaldi.repo ao ser instalado; se o playbook
    # o redefinisse, a segunda execução apareceria como "changed" (e brigaria com o pacote).
    reposdir = tmp_path / "repos"
    reposdir.mkdir()
    vendor = "[vivaldi]\nname=vivaldi\nbaseurl=https://x/y\nenabled=1\ngpgcheck=1\n# do pacote\n"
    (reposdir / "vivaldi.repo").write_text(vendor)

    first = _run_repos_role(tmp_path, reposdir, ["vivaldi", "novo"])
    assert first.returncode == 0, first.stdout + first.stderr
    assert (reposdir / "vivaldi.repo").read_text() == vendor
    created = (reposdir / "novo.repo").read_text()
    assert "[novo]" in created and "gpgcheck = 1" in created  # novo repo: assinatura ligada

    second = _run_repos_role(tmp_path, reposdir, ["vivaldi", "novo"])
    assert second.returncode == 0, second.stdout + second.stderr
    assert "changed=0" in second.stdout
    assert (reposdir / "vivaldi.repo").read_text() == vendor


@pytest.mark.skipif(not _collection_available(), reason="ansible-core + community.general ausentes")
def test_repos_role_skips_ids_defined_under_a_different_file_name(tmp_path):
    reposdir = tmp_path / "repos"
    reposdir.mkdir()
    (reposdir / "fedora-recovery-code.repo").write_text("[code]\nname=Visual Studio Code\n")
    run = _run_repos_role(tmp_path, reposdir, ["code"])
    assert run.returncode == 0, run.stdout + run.stderr
    assert sorted(p.name for p in reposdir.iterdir()) == ["fedora-recovery-code.repo"]
