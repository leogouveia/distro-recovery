from pathlib import Path

import pytest
from typer.testing import CliRunner

from distro_recovery import cli
from distro_recovery.cli import app
from distro_recovery.decisions_template import EXAMPLE_YAML, render_decisions_template
from distro_recovery.merge import load_decisions

FIX = Path(__file__).parent / "fixtures" / "dnf5"
PENDING = {
    "packages": [
        {"name": "chatgpt", "reason_code": "local_rpm"},
        {"name": "tmux", "reason_code": "user_unconfirmed"},
    ],
    "repos": [{"id": "meu-repo", "warnings": ["local_gpgkey: sem URL remota"]}],
    "missing_repos": ["sumiu"],
}


def test_template_is_valid_yaml_that_changes_nothing(tmp_path):
    f = tmp_path / "decisions.yml"
    f.write_text(render_decisions_template(None))
    d = load_decisions(f)
    assert (d.include, d.exclude, d.repos_exclude, d.repos_gpgkey, d.swaps) == (
        frozenset(),
        frozenset(),
        frozenset(),
        {},
        [],
    )


def test_template_explains_every_section_in_plain_words():
    text = render_decisions_template(None)
    for word in ("include", "exclude", "repos", "gpgkey", "swaps", "pending.yml"):
        assert word in text
    assert "nunca altera" in text  # a ferramenta não mexe neste arquivo depois de criado
    assert "exclude vence" in text  # precedência
    assert text.splitlines()[0].startswith("#")


def test_example_block_is_real_yaml_and_is_commented_out_in_the_template(tmp_path):
    f = tmp_path / "decisions.yml"
    f.write_text(EXAMPLE_YAML)
    d = load_decisions(f)
    assert d.include and d.exclude and d.repos_exclude and d.repos_gpgkey and d.swaps
    text = render_decisions_template(None)
    for line in EXAMPLE_YAML.splitlines():
        assert (f"# {line}" if line else "#") in text.splitlines()
    assert not any(ln and not ln.startswith("#") for ln in text.splitlines())


def test_template_lists_pending_items_as_commented_suggestions():
    text = render_decisions_template(PENDING)
    assert '#   - "chatgpt"' in text and "local_rpm" in text
    assert '#   - "tmux"' in text and "user_unconfirmed" in text
    assert "meu-repo" in text and "sumiu" in text
    assert not any(ln and not ln.startswith("#") for ln in text.splitlines())


def test_template_without_pending_says_so():
    assert "nenhuma pendência" in render_decisions_template({"packages": [], "repos": []})


def test_template_is_deterministic_and_has_no_timestamp():
    a = render_decisions_template(PENDING)
    assert a == render_decisions_template(PENDING)
    import re

    assert not re.search(r"\d{4}-\d{2}-\d{2}", a)


def init(tmp_path, *args):
    return CliRunner().invoke(app, ["init", "--dir", str(tmp_path), *args])


def test_init_creates_the_file_and_parent_dirs(tmp_path):
    target = tmp_path / "novo" / "dir"
    res = CliRunner().invoke(app, ["init", "--dir", str(target)])
    assert res.exit_code == 0, res.output
    assert (target / "decisions.yml").is_file() and "decisions.yml" in res.output


def test_init_never_overwrites_an_existing_file(tmp_path):
    f = tmp_path / "decisions.yml"
    f.write_text('exclude: ["meu"]\n')
    res = init(tmp_path)
    assert res.exit_code == 0 and "já existe" in res.output
    assert f.read_text() == 'exclude: ["meu"]\n'


def test_init_prefills_suggestions_from_the_last_backup(tmp_path):
    CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX), "--dir", str(tmp_path)])
    init(tmp_path)
    text = (tmp_path / "decisions.yml").read_text()
    assert '#   - "chatgpt"' in text and "local_rpm" in text
    assert load_decisions(tmp_path / "decisions.yml").exclude == frozenset()


def test_init_then_uncommenting_works_end_to_end(tmp_path):
    CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX), "--dir", str(tmp_path)])
    init(tmp_path)
    f = tmp_path / "decisions.yml"
    f.write_text(f.read_text() + '\nexclude:\n  - "chatgpt"\n')
    res = CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX), "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.output  # a única pendência foi decidida


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(cli, "_now", lambda: "2026-10-17T22:00:00Z")


def test_backup_hints_at_init_only_when_decisions_file_is_missing(tmp_path):
    first = CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX), "--dir", str(tmp_path)])
    assert "distro-recovery init" in first.output
    init(tmp_path)
    again = CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX), "--dir", str(tmp_path)])
    assert "distro-recovery init" not in again.output


def test_backup_itself_still_never_creates_decisions_yml(tmp_path):
    CliRunner().invoke(app, ["backup", "--from-fixtures", str(FIX), "--dir", str(tmp_path)])
    assert not (tmp_path / "decisions.yml").exists()
