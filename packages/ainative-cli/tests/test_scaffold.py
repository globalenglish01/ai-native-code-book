from __future__ import annotations

import pytest
from ainative_cli.scaffold import ProjectAlreadyExistsError, scaffold_project
from ainative_cli.templates import get_template


def test_scaffold_project_creates_expected_files(tmp_path):
    target = tmp_path / "my_project"
    template = get_template("minimal")

    written = scaffold_project(target, "my_project", template)

    names = {p.name for p in written}
    assert names == {"pyproject.toml", "README.md", "main.py", ".env.example"}
    for path in written:
        assert path.exists()


def test_scaffold_project_pyproject_includes_declared_packages(tmp_path):
    target = tmp_path / "my_project"
    template = get_template("customer-service")

    scaffold_project(target, "my_project", template)
    content = (target / "pyproject.toml").read_text(encoding="utf-8")

    for pkg in template.packages:
        assert pkg in content


def test_scaffold_project_readme_mentions_project_name_and_type(tmp_path):
    target = tmp_path / "my_project"
    template = get_template("browser-agent")

    scaffold_project(target, "my_project", template)
    readme = (target / "README.md").read_text(encoding="utf-8")

    assert "my_project" in readme
    assert "browser-agent" in readme


def test_scaffold_project_raises_on_existing_nonempty_dir_without_force(tmp_path):
    target = tmp_path / "my_project"
    target.mkdir()
    (target / "existing_file.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(ProjectAlreadyExistsError):
        scaffold_project(target, "my_project", get_template("minimal"))


def test_scaffold_project_with_force_overwrites_existing_dir(tmp_path):
    target = tmp_path / "my_project"
    target.mkdir()
    (target / "existing_file.txt").write_text("hello", encoding="utf-8")

    written = scaffold_project(target, "my_project", get_template("minimal"), force=True)
    assert len(written) == 4
    # The pre-existing unrelated file should still be there — force only allows
    # writing over the directory, it doesn't wipe it first.
    assert (target / "existing_file.txt").exists()


def test_scaffold_project_into_empty_existing_directory_does_not_raise(tmp_path):
    target = tmp_path / "my_project"
    target.mkdir()  # exists but empty

    written = scaffold_project(target, "my_project", get_template("minimal"))
    assert len(written) == 4
