from __future__ import annotations

import pytest

from ainative_cli.main import main


def test_new_command_creates_project(tmp_path, capsys):
    target_dir = tmp_path / "generated"
    exit_code = main(["new", "demo_project", "--type", "minimal", "--dir", str(target_dir)])

    assert exit_code == 0
    assert (target_dir / "main.py").exists()
    assert (target_dir / "pyproject.toml").exists()

    captured = capsys.readouterr()
    assert "demo_project" in captured.out


def test_new_command_defaults_to_minimal_type(tmp_path):
    target_dir = tmp_path / "generated"
    main(["new", "demo_project", "--dir", str(target_dir)])

    pyproject = (target_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert "ainative-core" in pyproject
    assert "ainative-guardrail" not in pyproject


def test_new_command_rejects_unknown_type(capsys):
    with pytest.raises(SystemExit):
        main(["new", "demo_project", "--type", "not-a-real-type"])


def test_new_command_fails_on_existing_nonempty_dir_without_force(tmp_path, capsys):
    target_dir = tmp_path / "generated"
    target_dir.mkdir()
    (target_dir / "something.txt").write_text("existing", encoding="utf-8")

    exit_code = main(["new", "demo_project", "--dir", str(target_dir)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()


def test_new_command_with_force_succeeds_on_existing_dir(tmp_path):
    target_dir = tmp_path / "generated"
    target_dir.mkdir()
    (target_dir / "something.txt").write_text("existing", encoding="utf-8")

    exit_code = main(["new", "demo_project", "--dir", str(target_dir), "--force"])
    assert exit_code == 0


def test_list_types_command_runs_without_error(capsys):
    exit_code = main(["list-types"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "customer-service" in captured.out
    assert "minimal" in captured.out


def test_no_command_prints_help_and_returns_error(capsys):
    exit_code = main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()
