import os
import shutil
import stat
import subprocess

import pytest
from typer.testing import CliRunner

from distro_recovery import cli, home_backup
from distro_recovery.cli import app
from distro_recovery.home_backup import create_backup, plan_backup


def run(source, destination, *args):
    return CliRunner().invoke(
        app,
        [
            "backup-home",
            "--source",
            str(source),
            "--destination",
            str(destination),
            *args,
        ],
    )


@pytest.fixture
def home(tmp_path):
    source = tmp_path / "home with spaces"
    source.mkdir()
    for name in (
        "Documents/único.txt",
        ".ssh/id_ed25519",
        ".gnupg/private",
        ".config/app/settings",
        "project/.git/config",
        "project/dist/unique",
        "project/build/manual",
        "project/.venv/unique",
        "project/node_modules/pkg/index.js",
        ".cache/cached",
        "project/__pycache__/foo.pyc",
        "project/.pytest_cache/state",
        ".npm/_cacache/data",
        ".npm/npmrc",
        ".local/share/app/data",
        "project/my_node_modules.txt",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    key = source / ".ssh/id_ed25519"
    key.chmod(0o600)
    os.utime(key, (1_700_000_000, 1_700_000_001))
    os.link(key, source / "hardlink")
    (source / "symlink").symlink_to("Documents/único.txt")
    return source


@pytest.fixture
def archive_tools():
    if not shutil.which("tar") or not shutil.which("zstd"):
        pytest.skip("integração exige GNU tar e zstd")
    home_backup.check_tools()


def test_real_archive_preserves_data_and_metadata(home, tmp_path, archive_tools):
    key = home / ".ssh/id_ed25519"
    xattrs = True
    try:
        os.setxattr(key, "user.backup_test", b"metadata")
    except OSError:
        xattrs = False
    destination = tmp_path / "backup.tar.zst"
    result = run(home, destination)
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    restored = tmp_path / "restored"
    restored.mkdir()
    subprocess.run(
        [
            "tar",
            "--extract",
            "--zstd",
            "--acls",
            "--xattrs",
            "--xattrs-include=*",
            "--same-permissions",
            "--file",
            str(destination),
            "--directory",
            str(restored),
        ],
        check=True,
    )
    for path in home.rglob("*"):
        rel = path.relative_to(home)
        if any(
            part in {"node_modules", "__pycache__", ".pytest_cache", ".cache", "_cacache"}
            for part in rel.parts
        ):
            assert not (restored / rel).exists()
        elif path.is_file():
            assert (restored / rel).read_bytes() == path.read_bytes()
    restored_key = restored / ".ssh/id_ed25519"
    assert stat.S_IMODE(restored_key.stat().st_mode) == 0o600
    assert restored_key.stat().st_mtime_ns == key.stat().st_mtime_ns
    assert restored_key.stat().st_uid == key.stat().st_uid
    assert restored_key.stat().st_gid == key.stat().st_gid
    assert restored_key.stat().st_ino == (restored / "hardlink").stat().st_ino
    assert os.readlink(restored / "symlink") == "Documents/único.txt"
    if xattrs:
        assert os.getxattr(restored_key, "user.backup_test") == b"metadata"
    assert not list(tmp_path.glob(".backup-home-*"))


def test_config_and_repeated_excludes(home, tmp_path, archive_tools):
    config = tmp_path / "rules.yml"
    config.write_text('exclude: ["./project/build", "./project/dist"]\n')
    destination = tmp_path / "custom.tar.zst"
    result = run(
        home,
        destination,
        "--config",
        str(config),
        "--exclude",
        "./.npm",
        "--exclude",
        "./project/.venv",
        "--no-default-excludes",
    )
    assert result.exit_code == 0, result.output
    listing = subprocess.check_output(["tar", "-tf", str(destination)], text=True)
    assert "./.cache/cached" in listing and "node_modules/pkg/index.js" in listing
    for excluded in ("./project/dist", "./project/build", "./.npm", "./project/.venv"):
        assert excluded not in listing


def test_dry_run_does_not_create_or_read_source_files(home, tmp_path, monkeypatch):
    monkeypatch.setattr(home_backup, "check_tools", lambda: None)
    monkeypatch.setattr(home_backup, "create_backup", lambda _: pytest.fail("dry-run criou backup"))
    destination = tmp_path / "dry.tar.zst"
    before = set(tmp_path.rglob("*"))
    result = run(home, destination, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "somente plano" in result.output and "node_modules" in result.output
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("kind", ["exists", "symlink", "inside", "missing_parent", "suffix"])
def test_invalid_destination_never_runs_tar(home, tmp_path, monkeypatch, kind):
    monkeypatch.setattr(home_backup, "check_tools", lambda: pytest.fail("validação tardia"))
    destination = tmp_path / "backup.tar.zst"
    if kind == "exists":
        destination.write_text("preserve")
    elif kind == "symlink":
        destination.symlink_to(tmp_path / "missing")
    elif kind == "inside":
        destination = home / "backup.tar.zst"
    elif kind == "missing_parent":
        destination = tmp_path / "missing" / "backup.tar.zst"
    elif kind == "suffix":
        destination = tmp_path / "backup.tar"
    result = run(home, destination)
    assert result.exit_code == 1, result.output
    if kind == "exists":
        assert destination.read_text() == "preserve"


@pytest.mark.parametrize(
    "content", ["exclude: nope", "exclude: [1]", "unknown: []", "[", "exclude: ['']"]
)
def test_invalid_config_is_clean_error(home, tmp_path, content):
    config = tmp_path / "rules.yml"
    config.write_text(content)
    result = run(home, tmp_path / "backup.tar.zst", "--config", str(config), "--dry-run")
    assert result.exit_code == 1 and "erro:" in result.output


@pytest.mark.parametrize("failed_step", [0, 1, 2])
def test_failure_in_creation_or_verification_removes_partial(
    home, tmp_path, monkeypatch, failed_step
):
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        if len(calls) == failed_step + 1:
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(home_backup.subprocess, "run", fake)
    destination = tmp_path / "fail.tar.zst"
    with pytest.raises(RuntimeError, match="Nenhum backup foi publicado"):
        create_backup(plan_backup(home, destination))
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup-home-*"))


def test_publish_never_overwrites_concurrent_file(home, tmp_path, monkeypatch):
    destination = tmp_path / "race.tar.zst"
    plan = plan_backup(home, destination)

    def fake(*args, **kwargs):
        destination.write_text("outro processo")
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(home_backup.subprocess, "run", fake)
    with pytest.raises(FileExistsError):
        create_backup(plan)
    assert destination.read_text() == "outro processo"
    assert not list(tmp_path.glob(".backup-home-*"))


def test_missing_tools(home, tmp_path, monkeypatch):
    monkeypatch.setattr(home_backup.shutil, "which", lambda _: None)
    result = run(home, tmp_path / "backup.tar.zst", "--dry-run")
    assert result.exit_code == 1 and "não encontrado" in result.output


def test_default_source_uses_home(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(home_backup, "check_tools", lambda: None)
    result = CliRunner().invoke(
        app,
        [
            "backup-home",
            "-o",
            str(tmp_path / "backup.tar.zst"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0 and str(home) in result.output


def test_acls_round_trip(home, tmp_path, archive_tools):
    if not shutil.which("setfacl") or not shutil.which("getfacl"):
        pytest.skip("integração ACL exige setfacl/getfacl")
    key = home / ".ssh/id_ed25519"
    acl_result = subprocess.run(["setfacl", "-m", "u:65534:r--", str(key)], capture_output=True)
    if acl_result.returncode:
        pytest.skip("sistema de arquivos não suporta ACLs")
    destination = tmp_path / "acl.tar.zst"
    assert run(home, destination).exit_code == 0
    restored = tmp_path / "acl-restored"
    restored.mkdir()
    subprocess.run(
        [
            "tar",
            "--extract",
            "--zstd",
            "--acls",
            "--file",
            str(destination),
            "--directory",
            str(restored),
        ],
        check=True,
    )
    before = subprocess.check_output(["getfacl", "-c", "-n", str(key)])
    after = subprocess.check_output(["getfacl", "-c", "-n", str(restored / ".ssh/id_ed25519")])
    assert before == after


def test_external_symlink_is_not_followed(home, tmp_path, archive_tools):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("não copiar")
    (home / "external").symlink_to(outside, target_is_directory=True)
    destination = tmp_path / "links.tar.zst"
    assert run(home, destination).exit_code == 0
    listing = subprocess.check_output(["tar", "-tf", str(destination)], text=True)
    assert "./external\n" in listing
    assert "external/secret" not in listing


def test_tar_environment_cannot_override_selection(home, tmp_path, archive_tools, monkeypatch):
    monkeypatch.setenv("TAR_OPTIONS", "--exclude=*")
    destination = tmp_path / "env.tar.zst"
    assert run(home, destination).exit_code == 0
    monkeypatch.delenv("TAR_OPTIONS")
    listing = subprocess.check_output(["tar", "-tf", str(destination)], text=True)
    assert "./Documents/único.txt" in listing


@pytest.mark.parametrize("source", ["missing", "file", "root"])
def test_invalid_source(tmp_path, source):
    origin = tmp_path / "missing"
    if source == "file":
        origin.write_text("not a directory")
    elif source == "root":
        origin = tmp_path.anchor
    result = run(origin, tmp_path / "home.tar.zst", "--dry-run")
    assert result.exit_code == 1 and "erro:" in result.output


def test_verbose_lists_files_and_reports_verification_stages(home, tmp_path, archive_tools, capfd):
    destination = tmp_path / "verbose.tar.zst"
    result = run(home, destination, "--verbose")
    output = capfd.readouterr().out
    assert result.exit_code == 0, result.output
    assert "./Documents/" in output and "./.ssh/id_ed25519" in output
    assert "./project/node_modules/" not in output
    assert "[1/3]" in result.output and "[2/3]" in result.output and "[3/3]" in result.output
    assert "backup da HOME verificado" in result.output


def test_cli_creation_failure_explains_no_published_backup(home, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "check_tools", lambda: None)

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(2, argv)

    monkeypatch.setattr(home_backup.subprocess, "run", fail)
    destination = tmp_path / "failed.tar.zst"
    result = run(home, destination)
    assert result.exit_code == 1
    assert "criação do tar+zstd falhou" in result.output
    assert "Nenhum backup foi publicado" in result.output
    assert "socket ignorado" in result.output
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup-home-*"))


def test_socket_warning_is_not_a_backup_failure(home, tmp_path, archive_tools):
    import socket

    with socket.socket(socket.AF_UNIX) as sock:
        # Caminho curto para respeitar o limite de sockaddr_un.
        socket_path = home / "app.sock"
        if len(os.fsencode(socket_path)) >= 108:
            pytest.skip("caminho temporário excede limite de socket Unix")
        try:
            sock.bind(str(socket_path))
        except PermissionError:
            pytest.skip("ambiente impede criar sockets Unix")
        destination = tmp_path / "socket.tar.zst"
        result = run(home, destination)
    assert result.exit_code == 0, result.output
    assert destination.exists()
    listing = subprocess.check_output(["tar", "-tf", str(destination)], text=True)
    assert "app.sock" not in listing
    assert "./Documents/" in listing


def test_dry_run_verbose_shows_listing_option(home, tmp_path, archive_tools):
    result = run(home, tmp_path / "plan.tar.zst", "--dry-run", "--verbose")
    assert result.exit_code == 0
    assert "--verbose" in result.output
    assert "--atime-preserve" not in result.output


@pytest.mark.parametrize(
    "diagnostic",
    [
        "tar: ./busy: file changed as we read it\n",
        "tar: ./busy: File shrank by 32 bytes; padding with zeros\n",
    ],
)
def test_changed_files_are_published_after_verification(
    home, tmp_path, archive_tools, monkeypatch, diagnostic
):
    real_run = subprocess.run
    calls = []

    def changing_tar(argv, **kwargs):
        result = real_run(argv, **kwargs)
        calls.append(argv)
        if "--create" in argv:
            kwargs["stderr"].write(diagnostic)
            kwargs["stderr"].flush()
            return subprocess.CompletedProcess(argv, 1)
        return result

    monkeypatch.setattr(home_backup.subprocess, "run", changing_tar)
    destination = tmp_path / "changing.tar.zst"
    result = run(home, destination)
    assert result.exit_code == 0, result.output
    assert "concluído com avisos" in result.output
    assert "podem estar inconsistentes" in result.output
    assert destination.exists()
    assert any("--test" in argv for argv in calls)
    assert any("--list" in argv for argv in calls)
    assert not list(tmp_path.glob(".backup-home-*"))


@pytest.mark.parametrize(
    ("code", "diagnostics"),
    [
        (1, ""),
        (1, "zstd: compressor failed\n"),
        (1, "tar: ./busy: file changed as we read it\nzstd: compressor failed\n"),
        (
            1,
            "tar: ./busy: file changed as we read it\n"
            "tar: ./private: Cannot open: Permission denied\n",
        ),
        (2, "tar: ./busy: file changed as we read it\n"),
    ],
)
def test_other_creation_failures_still_abort(home, tmp_path, monkeypatch, code, diagnostics):
    def fake(argv, **kwargs):
        kwargs["stderr"].write(diagnostics)
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(home_backup.subprocess, "run", fake)
    destination = tmp_path / "error.tar.zst"
    with pytest.raises(RuntimeError, match="Nenhum backup foi publicado"):
        create_backup(plan_backup(home, destination))
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup-home-*"))


def test_real_file_modified_during_tar_is_tolerated(home, tmp_path, archive_tools, monkeypatch):
    # Checkpoints tornam a alteração determinística, sem depender de corridas entre threads.
    busy = home / "busy.bin"
    busy.write_bytes(b"x" * (1024 * 1024))
    monkeypatch.setenv("BACKUP_TEST_BUSY", str(busy))
    original = home_backup.BackupPlan.command

    def command(self, output, *, verbose=False):
        argv = original(self, output, verbose=verbose)
        argv[1:1] = ["--checkpoint=10", '--checkpoint-action=exec=touch -- "$BACKUP_TEST_BUSY"']
        return argv

    monkeypatch.setattr(home_backup.BackupPlan, "command", command)
    destination = tmp_path / "real-change.tar.zst"
    result = run(home, destination)
    assert result.exit_code == 0, result.output
    assert "file changed as we read it" in result.output
    assert "concluído com avisos" in result.output
    assert destination.exists()


@pytest.mark.parametrize("failed_stage", ["--test", "--list"])
def test_verification_still_required_after_changed_files(
    home, tmp_path, archive_tools, monkeypatch, failed_stage
):
    real_run = subprocess.run

    def fake(argv, **kwargs):
        if failed_stage in argv:
            raise subprocess.CalledProcessError(1, argv)
        result = real_run(argv, **kwargs)
        if "--create" in argv:
            kwargs["stderr"].write("tar: ./busy: file changed as we read it\n")
            return subprocess.CompletedProcess(argv, 1)
        return result

    monkeypatch.setattr(home_backup.subprocess, "run", fake)
    destination = tmp_path / "invalid.tar.zst"
    result = run(home, destination)
    assert result.exit_code == 1
    assert "Nenhum backup foi publicado" in result.output
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup-home-*"))
