import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pyte
import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "rolling_box.py"
spec = importlib.util.spec_from_file_location("rolling_box", SCRIPT)
rb = importlib.util.module_from_spec(spec)
sys.modules["rolling_box"] = rb
spec.loader.exec_module(rb)

ROWS, COLS, N = 24, 60, 6


def make_box(**kw):
    out = io.StringIO()
    box = rb.Box(out, lines=N, title="restore", tty=True, columns=COLS, **kw)
    return box, out


def screen_of(out):
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.Stream(screen)
    stream.feed(out.getvalue().replace("\n", "\r\n"))
    return [ln.rstrip() for ln in screen.display]


def visible(out):
    return [ln for ln in screen_of(out) if ln]


def test_box_keeps_a_fixed_height_no_matter_how_much_output_arrives():
    box, out = make_box()
    for i in range(500):
        box.add(f"linha {i}")
        box.flush()
    rows = visible(out)
    assert len(rows) == N + 2  # bordas + N linhas, nada além disso
    text = "\n".join(rows)
    assert "linha 499" in text and "linha 494" in text
    assert "linha 493" not in text and "linha 0" not in text


def test_box_has_borders_and_title():
    box, out = make_box()
    box.add("oi")
    box.flush()
    rows = visible(out)
    assert rows[0].startswith("┌") and "restore" in rows[0] and rows[-1].startswith("└")
    assert all(len(r) <= COLS for r in rows)


def test_long_lines_are_truncated_to_the_terminal_width():
    box, out = make_box()
    box.add("x" * 500)
    box.flush()
    assert all(len(r) <= COLS for r in screen_of(out))
    assert len(visible(out)) == N + 2


def test_passthrough_lines_appear_once_above_the_box():
    box, out = make_box()
    for i in range(20):
        box.add(f"ruido {i}")
    box.passthrough("PASS  primeiro")
    for i in range(20, 40):
        box.add(f"ruido {i}")
    box.passthrough("FAIL  segundo")
    box.flush()
    rows = visible(out)
    assert rows[0] == "PASS  primeiro" and rows[1] == "FAIL  segundo"
    assert len(rows) == 2 + N + 2
    assert sum("PASS  primeiro" in r for r in rows) == 1


def test_close_removes_the_box_and_leaves_one_summary_line():
    box, out = make_box()
    for i in range(50):
        box.add(f"linha {i}")
    box.passthrough("PASS  ok")
    box.close()
    rows = visible(out)
    assert rows[0] == "PASS  ok"
    assert len(rows) == 2 and "50" in rows[1] and "restore" in rows[1]
    assert not any(r.startswith(("┌", "│", "└")) for r in rows)


def test_clean_strips_colors_carriage_returns_and_control_chars():
    assert rb.clean("\x1b[1;32mok\x1b[0m") == "ok"
    assert rb.clean("baixando 10%\rbaixando 99%\r") == "baixando 99%"
    assert rb.clean("a\x07b\x00c") == "abc"
    assert rb.clean("a\tb") == "a   b"


def run_cli(stdin, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_cli_plain_mode_when_not_a_terminal_prefixes_box_lines():
    out = run_cli("@@ ruido\nPASS  ok\n@@ mais ruido\n", "--title", "x")
    assert out.splitlines() == ["  | ruido", "PASS  ok", "  | mais ruido"]


def test_cli_all_mode_boxes_every_line():
    out = run_cli("a\nb\n", "--all", "--title", "x")
    assert out.splitlines() == ["  | a", "  | b"]


def test_cli_handles_missing_trailing_newline():
    assert run_cli("@@ ultima", "--title", "x") == "  | ultima\n"


@pytest.mark.parametrize("bad", ["--lines", "0"])
def test_cli_rejects_invalid_line_count(bad):
    args = ["--lines", "0"] if bad == "0" else ["--lines"]
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args], input="", capture_output=True, text=True, check=False
    )
    assert proc.returncode != 0
