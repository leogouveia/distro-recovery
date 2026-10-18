from pathlib import Path

from ruamel.yaml import YAML

from distro_recovery.merge import build_vars, load_decisions

SMOKE = Path(__file__).parent / "vm" / "smoke"


def test_smoke_dataset_produces_the_variables_the_vm_test_expects():
    generated = YAML(typ="safe").load((SMOKE / "generated" / "fedora.yml").read_text())
    data = build_vars(generated, load_decisions(SMOKE / "decisions.yml"))
    assert data["dr_packages"] == [
        "1password-cli",
        "htop",
        "pacote-que-nao-existe-xyz",
        "vivaldi-stable",
        "zsh",
    ]
    assert data["dr_groups"] == ["rpm-development-tools"]
    assert [r["id"] for r in data["dr_repos"]] == ["1password", "vivaldi"]
