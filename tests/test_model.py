import dataclasses

import pytest

from distro_recovery.model import (
    Baseline,
    Classified,
    Confidence,
    Group,
    InstalledPkg,
    Reason,
    ReasonCode,
    RepoKind,
    RepoSpec,
    parse_nevra,
)


def test_reason_values_match_dnf5_output():
    assert {r.value for r in Reason} == {
        "User",
        "Group",
        "Dependency",
        "Weak Dependency",
        "Clean",
    }


def test_reason_from_dnf():
    assert Reason("Weak Dependency") is Reason.WEAK_DEPENDENCY


def test_confidence_values():
    assert [c.value for c in Confidence] == ["high", "review", "excluded"]


def test_reason_codes_cover_decision_table():
    assert {c.value for c in ReasonCode} == {
        "user_excluded",
        "user_included",
        "system_rule",
        "dependency",
        "via_group",
        "baseline_group",
        "user_history",
        "user_leaf",
        "user_unconfirmed",
        "in_baseline_but_user",
        "baseline",
        "local_rpm",
        "duplicated_in_brew",
    }


def test_installed_pkg_is_frozen_and_hashable():
    pkg = InstalledPkg("vlc", "x86_64", Reason.USER, "updates", "Fedora Project")
    with pytest.raises(dataclasses.FrozenInstanceError):
        pkg.name = "other"  # type: ignore[misc]
    assert pkg in {pkg}


def test_installed_pkg_is_local():
    local = InstalledPkg("chatgpt", "x86_64", Reason.USER, "@commandline", "")
    repo = InstalledPkg("vlc", "x86_64", Reason.USER, "updates", "")
    assert local.is_local and not repo.is_local


def test_classified_defaults():
    c = Classified("vlc", Confidence.HIGH, ReasonCode.USER_HISTORY)
    assert c.group is None


def test_group_and_baseline_are_frozen():
    g = Group("development-tools", "Development Tools", frozenset({"git"}))
    b = Baseline(
        environment="workstation-product-environment",
        groups=frozenset({"core"}),
        packages=frozenset({"bash"}),
        protected=frozenset({"gnome-shell"}),
        system_release=frozenset({"fedora-release-workstation"}),
    )
    assert "git" in g.packages and "core" in b.groups
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.groups = frozenset()  # type: ignore[misc]


def test_repo_kinds():
    assert {k.value for k in RepoKind} == {
        "official",
        "third-party",
        "release-package",
        "copr",
        "repofile",
    }


def test_repo_spec_defaults():
    r = RepoSpec(id="vivaldi", kind=RepoKind.REPOFILE, name="vivaldi")
    assert r.baseurl is None and r.gpgkeys == () and r.warnings == ()


@pytest.mark.parametrize(
    ("nevra", "expected"),
    [
        ("pavucontrol-0:6.2-1.fc44.x86_64", ("pavucontrol", "0", "6.2", "1.fc44", "x86_64")),
        ("1password-0:8.12.40-1.x86_64", ("1password", "0", "8.12.40", "1", "x86_64")),
        (
            "NetworkManager-config-connectivity-fedora-1:1.56.1-2.fc44.noarch",
            ("NetworkManager-config-connectivity-fedora", "1", "1.56.1", "2.fc44", "noarch"),
        ),
    ],
)
def test_parse_nevra(nevra, expected):
    assert tuple(parse_nevra(nevra)) == expected


def test_parse_nevra_rejects_garbage():
    with pytest.raises(ValueError):
        parse_nevra("not a nevra")
