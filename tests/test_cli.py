import socket

import pytest

import discovery
from discovery import cli


def make_project(root, skip=()):
    for rel in cli.REQUIRED_DIRS:
        if rel not in skip:
            (root / rel).mkdir(parents=True, exist_ok=True)
    (root / ".env.example").write_text("X=1\n")
    (root / ".env").write_text("X=1\n")


def statuses(results):
    return [s for s, _ in results]


def test_package_imports():
    assert discovery.__version__


def test_doctor_command_runs_and_exits_zero(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "Summary" in out


def test_doctor_detects_required_folders(tmp_path):
    make_project(tmp_path)
    results = cli.run_checks(tmp_path)
    assert cli.FAIL not in statuses(results)
    for rel in cli.REQUIRED_DIRS:
        assert any(s == cli.PASS and f"{rel}/" in m for s, m in results)


def test_missing_folder_gives_understandable_error(tmp_path):
    make_project(tmp_path, skip=("config",))
    results = cli.run_checks(tmp_path)
    assert any(s == cli.FAIL and "config/" in m and "missing" in m for s, m in results)


def test_uninitialized_database_is_warning_not_failure(tmp_path):
    make_project(tmp_path)
    results = cli.run_checks(tmp_path)
    assert any(s == cli.WARNING and "not initialized" in m for s, m in results)
    assert cli.FAIL not in statuses(results)


def test_doctor_does_not_use_network(monkeypatch, capsys):
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    assert cli.main(["doctor"]) == 0


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit) as exc:
        cli.main(["nonexistent"])
    assert exc.value.code != 0
