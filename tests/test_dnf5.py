import pytest

from distro_recovery.backends.dnf5 import (
    Dnf5Backend,
    normalize_url,
    parse_info_blocks,
    parse_installed,
    parse_leaves,
    parse_repo_file,
)
from distro_recovery.model import Reason, RepoKind


@pytest.fixture(scope="module")
def backend(runner):
    return Dnf5Backend(runner)


def test_detect(backend):
    assert backend.family == "fedora"
    assert backend.detect() is True


def test_parse_installed_rejects_malformed_line():
    with pytest.raises(ValueError, match="5 campos"):
        parse_installed("vlc\tx86_64\tUser\n")


def test_parse_installed_rejects_unknown_reason():
    with pytest.raises(ValueError, match="reason"):
        parse_installed("vlc\tx86_64\tWhatever\tupdates\tFedora\n")


def test_installed(backend):
    pkgs = backend.installed()
    assert len(pkgs) == 2373
    by_name = {p.name: p for p in pkgs}
    assert by_name["pavucontrol"].reason is Reason.USER
    assert by_name["7zip"].reason is Reason.WEAK_DEPENDENCY
    assert by_name["chatgpt"].is_local
    assert by_name["1password"].from_repo == "1password"


def test_installed_does_not_confuse_literal_backslash_t(backend):
    assert all("\\t" not in p.name for p in backend.installed())


def test_parse_info_blocks_continuations_and_multiple_entries():
    text = (
        "Id          : a\nInstalled   : True\nRequired groups : x\n"
        "                : y\n\nId          : a\nInstalled   : False\n"
    )
    blocks = parse_info_blocks(text)
    assert [b["Installed"] for b in blocks] == [["True"], ["False"]]
    assert blocks[0]["Required groups"] == ["x", "y"]


def test_installed_groups(backend):
    groups = {g.id: g for g in backend.installed_groups()}
    assert set(groups) == {
        "container-management",
        "desktop-accessibility",
        "development-tools",
        "libreoffice",
    }
    dev = groups["development-tools"]
    assert {"gettext", "git", "doxygen"} <= dev.packages  # mandatory + default
    assert "buildbot" not in dev.packages  # optional
    assert dev.name == "Development Tools"


def test_baseline(backend):
    b = backend.baseline()
    assert b.environment == "workstation-product-environment"
    assert len(b.groups) == 14 and {"core", "fonts", "libreoffice"} <= b.groups
    assert "development-tools" not in b.groups
    assert "bash" in b.packages
    assert "gnome-shell" in b.protected
    assert b.system_release == frozenset({"fedora-release-workstation"})


def test_history_user_installs(backend):
    h = backend.history_user_installs()
    assert h.first_tx_id == 1
    assert h.installs["pavucontrol"] == frozenset({45})
    assert 44 in h.installs["vlc"]
    assert "7zip" not in h.installs  # dependência, não User


def test_parse_leaves_extracts_names():
    assert parse_leaves("- 1password-0:8.12.40-1.x86_64\n- a-b-1:1.0-2.fc44.noarch\n") == {
        "1password",
        "a-b",
    }


def test_leaves(backend):
    leaves = backend.leaves()
    assert len(leaves) == 250 and "1password" in leaves  # 252 linhas; kernel x3


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x/rpm/stable/x86_64", "https://x/rpm/stable/$basearch"),
        ("https://x/fedora-44/y/aarch64/", "https://x/fedora-$releasever/y/$basearch/"),
        ("https://x/8.12.44/pkg", "https://x/8.12.44/pkg"),
        ("https://x/v144/pkg", "https://x/v144/pkg"),
        ("https://x/fedora-44-x86_64/", "https://x/fedora-$releasever-$basearch/"),
    ],
)
def test_normalize_url(url, expected):
    assert normalize_url(url, "44") == expected


NEEDED = {
    "1password",
    "code",
    "copr:copr.fedorainfracloud.org:kylegospo:webapp-manager",
    "copr:copr.fedorainfracloud.org:myriad-sun:android-studio",
    "fedora",
    "fedora-cisco-openh264",
    "gitlab.com_paulcarroty_vscodium_repo",
    "google-chrome",
    "rpmfusion-nonfree-nvidia-driver",
    "updates",
    "vivaldi",
    "ghost-repo",
}


def test_repos(backend):
    specs = {r.id: r for r in backend.repos(NEEDED)}
    assert "ghost-repo" not in specs  # sem arquivo: quem chama detecta como ausente
    assert specs["fedora"].kind is RepoKind.OFFICIAL
    assert specs["fedora-cisco-openh264"].kind is RepoKind.OFFICIAL
    assert specs["google-chrome"].kind is RepoKind.THIRD_PARTY
    assert specs["google-chrome"].package == "fedora-workstation-repositories"
    assert specs["rpmfusion-nonfree-nvidia-driver"].kind is RepoKind.THIRD_PARTY
    vivaldi = specs["vivaldi"]
    assert vivaldi.kind is RepoKind.REPOFILE and vivaldi.package is None
    assert vivaldi.gpgkey_urls == ("https://repo.vivaldi.com/archive/linux_signing_key.pub",)
    copr = specs["copr:copr.fedorainfracloud.org:kylegospo:webapp-manager"]
    assert copr.kind is RepoKind.COPR and copr.owner == "kylegospo/webapp-manager"


def test_repos_duplicate_id_first_file_wins_and_warns(backend):
    spec = {r.id: r for r in backend.repos({"1password"})}["1password"]
    assert spec.baseurl == "https://downloads.1password.com/linux/rpm/stable/$basearch"
    assert any(w.startswith("duplicate_repo_id") for w in spec.warnings)


def test_repos_normalizes_fixed_arch_and_release(backend):
    spec = {r.id: r for r in backend.repos({"gitlab.com_paulcarroty_vscodium_repo"})}[
        "gitlab.com_paulcarroty_vscodium_repo"
    ]
    assert "x86_64" not in (spec.baseurl or "")


def test_parse_repo_file_handles_colons_and_no_interpolation():
    text = "[copr:h:o:p]\nname=x\nbaseurl=https://h/$releasever/$basearch/\n"
    assert parse_repo_file(text)["copr:h:o:p"]["baseurl"] == "https://h/$releasever/$basearch/"


def _spec(opts, owner=None, sid="r"):
    return Dnf5Backend._spec(sid, owner, opts, "44", 1)


def test_spec_credentials_and_gpgcheck_warnings():
    spec = _spec({"baseurl": "https://user:tok@host/fedora-44/", "gpgcheck": "0"})
    codes = {w.split(":")[0] for w in spec.warnings}
    assert codes == {"credentials_in_url", "gpgcheck_disabled"}
    assert spec.kind is RepoKind.REPOFILE


def test_spec_release_package_kind():
    spec = _spec({"baseurl": "https://x/y"}, owner="rpmfusion-nonfree-release")
    assert spec.kind is RepoKind.RELEASE_PACKAGE
    assert spec.package == "rpmfusion-nonfree-release"


def test_spec_package_owned_repofile_warns():
    spec = _spec({"baseurl": "https://x/y"}, owner="chatgpt")
    assert spec.kind is RepoKind.REPOFILE and spec.package == "chatgpt"
    assert any(w.startswith("owned_by_package") for w in spec.warnings)


def test_spec_fingerprint_from_key_filename():
    fp = "3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4"
    spec = _spec({"gpgkey": f"file:///etc/pki/rpm-gpg/RPM-GPG-KEY-x-{fp}.asc"})
    assert spec.gpgkeys == (fp.lower(),)


def test_spec_local_gpgkey_without_owner_warns():
    spec = _spec({"gpgkey": "file:///etc/pki/rpm-gpg/fedora-recovery-code-0.gpg"})
    assert any(w.startswith("local_gpgkey") for w in spec.warnings)


def test_spec_local_gpgkey_shipped_by_a_package_is_fine():
    key = "file:///etc/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion"
    spec = _spec({"gpgkey": key}, owner="fedora-workstation-repositories")
    assert not any(w.startswith("local_gpgkey") for w in spec.warnings)


def test_repos_only_defined_by_recovery_files_have_local_key_warning(backend):
    spec = {r.id: r for r in backend.repos({"code"})}["code"]
    assert any(w.startswith("local_gpgkey") for w in spec.warnings)
