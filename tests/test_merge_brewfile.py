import pytest

from distro_recovery.brewfile import parse_brewfile
from distro_recovery.classify import DecisionConflict
from distro_recovery.merge import Decisions, DecisionsError, build_vars, load_decisions


def test_brewfile_names():
    text = """
tap "homebrew/bundle"
brew "htop"
brew 'bat', args: ["HEAD"]
brew "user/tap/eza"
cask "firefox"
# brew "comentado"
vscode "ms-python.python"
"""
    assert parse_brewfile(text) == {"htop", "bat", "eza"}


def test_brewfile_empty():
    assert parse_brewfile("") == frozenset()


def test_load_decisions_missing_file_is_empty(tmp_path):
    d = load_decisions(tmp_path / "decisions.yml")
    assert (d.include, d.exclude, d.repos_exclude) == (frozenset(), frozenset(), frozenset())


def test_load_decisions_reads_fields(tmp_path):
    f = tmp_path / "decisions.yml"
    f.write_text(
        'include: ["a", "b"]\nexclude: ["c"]\nrenames: {x: y}\n'
        'swaps: [{from: "ffmpeg-free", to: "ffmpeg"}]\nrepos:\n  exclude: ["vivaldi"]\n'
    )
    d = load_decisions(f)
    assert d.include == {"a", "b"} and d.exclude == {"c"} and d.repos_exclude == {"vivaldi"}
    assert d.renames == {"x": "y"} and d.swaps == [{"from": "ffmpeg-free", "to": "ffmpeg"}]


def test_load_decisions_conflict(tmp_path):
    f = tmp_path / "decisions.yml"
    f.write_text('include: ["a"]\nexclude: ["a"]\n')
    with pytest.raises(DecisionConflict, match="a"):
        load_decisions(f)


@pytest.mark.parametrize("body", ["include: nope\n", "- 1\n", "repos: [x]\n", "include: [1]\n"])
def test_load_decisions_rejects_bad_shapes(tmp_path, body):
    f = tmp_path / "decisions.yml"
    f.write_text(body)
    with pytest.raises(DecisionsError):
        load_decisions(f)


def test_load_decisions_repo_gpgkey_overrides(tmp_path):
    f = tmp_path / "decisions.yml"
    f.write_text('repos:\n  gpgkey:\n    code: "https://example.org/k.asc"\n')
    assert load_decisions(f).repos_gpgkey == {"code": "https://example.org/k.asc"}


@pytest.mark.parametrize("body", ["repos:\n  gpgkey: [x]\n", "repos:\n  gpgkey: {code: 1}\n"])
def test_load_decisions_rejects_bad_gpgkey_overrides(tmp_path, body):
    f = tmp_path / "decisions.yml"
    f.write_text(body)
    with pytest.raises(DecisionsError):
        load_decisions(f)


GENERATED = {
    "family": "fedora",
    "repos": [
        {"id": "a", "kind": "repofile", "name": "A", "gpgkey_urls": ["https://old/k"]},
        {"id": "b", "kind": "copr", "name": "B", "owner": "o/p"},
    ],
    "groups": ["development-tools"],
    "packages": ["vlc", "htop"],
}


def test_build_vars_defaults_pass_generated_through_sorted():
    v = build_vars(GENERATED, Decisions())
    assert v["dr_family"] == "fedora"
    assert v["dr_packages"] == ["htop", "vlc"]
    assert v["dr_groups"] == ["development-tools"]
    assert [r["id"] for r in v["dr_repos"]] == ["a", "b"]
    assert v["dr_swaps"] == []


def test_build_vars_precedence_exclude_over_include_over_generated():
    d = Decisions(include=frozenset({"extra"}), exclude=frozenset({"vlc"}))
    assert build_vars(GENERATED, d)["dr_packages"] == ["extra", "htop"]


def test_build_vars_exclude_wins_even_when_generated_has_it():
    d = Decisions(exclude=frozenset({"htop", "vlc"}))
    assert build_vars(GENERATED, d)["dr_packages"] == []


def test_build_vars_include_and_exclude_conflict():
    d = Decisions(include=frozenset({"x"}), exclude=frozenset({"x"}))
    with pytest.raises(DecisionConflict, match="x"):
        build_vars(GENERATED, d)


def test_build_vars_repo_exclude_and_gpgkey_override():
    d = Decisions(repos_exclude=frozenset({"b"}), repos_gpgkey={"a": "https://new/k"})
    repos = build_vars(GENERATED, d)["dr_repos"]
    assert [r["id"] for r in repos] == ["a"] and repos[0]["gpgkey_urls"] == ["https://new/k"]


def test_build_vars_swaps_pass_through():
    d = Decisions(swaps=[{"from": "ffmpeg-free", "to": "ffmpeg"}])
    assert build_vars(GENERATED, d)["dr_swaps"] == [{"from": "ffmpeg-free", "to": "ffmpeg"}]


def test_build_vars_does_not_mutate_inputs():
    before = repr(GENERATED)
    build_vars(GENERATED, Decisions(repos_gpgkey={"a": "https://new/k"}))
    assert repr(GENERATED) == before


@pytest.mark.parametrize(
    "body", ["swaps: [{from: a}]\n", "swaps: [x]\n", "swaps: [{from: a, to: 1}]\n"]
)
def test_load_decisions_rejects_bad_swaps(tmp_path, body):
    f = tmp_path / "decisions.yml"
    f.write_text(body)
    with pytest.raises(DecisionsError):
        load_decisions(f)
