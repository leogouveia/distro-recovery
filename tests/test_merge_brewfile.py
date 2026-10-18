import pytest

from distro_recovery.brewfile import parse_brewfile
from distro_recovery.classify import DecisionConflict
from distro_recovery.merge import DecisionsError, load_decisions


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
