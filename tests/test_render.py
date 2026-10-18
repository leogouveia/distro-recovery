import random
import re

from ruamel.yaml import YAML

from distro_recovery.classify import split_repos
from distro_recovery.model import Classified, Confidence, ReasonCode, RepoKind, RepoSpec
from distro_recovery.render import render_fedora, render_meta, render_pending

REPOS = [
    RepoSpec(
        id="vivaldi",
        kind=RepoKind.REPOFILE,
        name="vivaldi",
        baseurl="https://repo.vivaldi.com/archive/rpm/$basearch",
        gpgkeys=("bb", "aa"),
        gpgkey_urls=("https://z/key", "https://a/key"),
    ),
    RepoSpec(
        id="copr:h:o:p",
        kind=RepoKind.COPR,
        name="copr",
        owner="o/p",
        warnings=("duplicate_repo_id: 2 arquivos",),
    ),
]
BAD = RepoSpec(
    id="priv", kind=RepoKind.REPOFILE, name="priv", warnings=("gpgcheck_disabled: gpgcheck=0",)
)
PKGS = ["zsh", "alpha", "htop", "alpha"]
GROUPS = ["libreoffice", "development-tools"]
PENDING = [
    Classified("tmux", Confidence.REVIEW, ReasonCode.USER_UNCONFIRMED),
    Classified("chatgpt", Confidence.REVIEW, ReasonCode.LOCAL_RPM),
]
LOAD = YAML(typ="safe").load
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_fedora_is_deterministic_and_input_order_independent():
    a = render_fedora(PKGS, GROUPS, REPOS)
    for _ in range(5):
        pk, gr, rp = list(PKGS), list(GROUPS), list(REPOS)
        for lst in (pk, gr, rp):
            random.shuffle(lst)
        assert render_fedora(pk, gr, rp) == a


def test_fedora_structure_and_key_order():
    out = render_fedora(PKGS, GROUPS, REPOS)
    data = LOAD(out)
    assert list(data) == ["family", "repos", "groups", "packages"]
    assert data["packages"] == ["alpha", "htop", "zsh"]  # ordenado, sem duplicatas
    assert data["groups"] == ["development-tools", "libreoffice"]
    assert [r["id"] for r in data["repos"]] == ["copr:h:o:p", "vivaldi"]
    viv = data["repos"][1]
    assert list(viv) == ["id", "kind", "name", "baseurl", "gpgkeys", "gpgkey_urls"]
    assert viv["gpgkeys"] == ["aa", "bb"] and viv["gpgkey_urls"] == [
        "https://a/key",
        "https://z/key",
    ]
    assert "warnings" not in data["repos"][0]  # avisos só no pending
    assert data["repos"][0]["owner"] == "o/p"


def test_strings_are_always_double_quoted():
    out = render_fedora(PKGS, GROUPS, REPOS)
    scalar = re.compile(r"^\s*(?:- )?(?:[A-Za-z_]+: )?(?P<v>\S.*)$")
    checked = 0
    for line in out.splitlines():
        if line.startswith("#") or line.rstrip().endswith(":"):
            continue
        value = scalar.match(line).group("v")
        if value == "[]":
            continue
        assert value.startswith('"') and value.endswith('"'), line
        checked += 1
    assert checked > 10


def test_numeric_looking_names_stay_strings():
    data = LOAD(render_fedora(["7zip", "0ad", "yes", "null"], [], []))
    assert data["packages"] == ["0ad", "7zip", "null", "yes"]


def test_empty_lists_render_as_flow_empty():
    data = LOAD(render_fedora([], [], []))
    assert data == {"family": "fedora", "repos": [], "groups": [], "packages": []}


def test_no_timestamps_outside_meta():
    assert not ISO.search(render_fedora(PKGS, GROUPS, REPOS))
    assert not ISO.search(render_pending(PENDING, [BAD], ["ghost"]))


def test_pending_is_deterministic_and_ordered():
    a = render_pending(PENDING, [BAD], ["ghost"])
    assert render_pending(list(reversed(PENDING)), [BAD], ["ghost"]) == a
    data = LOAD(a)
    assert list(data) == ["packages", "repos", "missing_repos"]
    assert data["packages"] == [
        {"name": "chatgpt", "reason_code": "local_rpm"},
        {"name": "tmux", "reason_code": "user_unconfirmed"},
    ]
    assert data["repos"][0]["warnings"] == ["gpgcheck_disabled: gpgcheck=0"]
    assert data["missing_repos"] == ["ghost"]


def test_meta_has_timestamp_and_is_deterministic_for_same_input():
    kw = {"family": "fedora", "tool_version": "0.1.0", "generated_at": "2026-10-17T22:00:00Z"}
    counts = {"packages": 3, "pending": 2}
    a = render_meta(**kw, counts=counts)
    assert render_meta(**kw, counts=dict(reversed(counts.items()))) == a
    data = LOAD(a)
    assert list(data) == ["family", "tool_version", "generated_at", "counts"]
    assert data["generated_at"] == "2026-10-17T22:00:00Z"


def test_split_repos_blocks_only_on_security_warnings():
    ok, pending = split_repos([*REPOS, BAD])
    assert {r.id for r in ok} == {"vivaldi", "copr:h:o:p"}
    assert [r.id for r in pending] == ["priv"]


def test_split_repos_drops_official():
    off = RepoSpec(id="fedora", kind=RepoKind.OFFICIAL, name="Fedora")
    ok, pending = split_repos([off])
    assert ok == [] and pending == []
