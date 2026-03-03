"""Tests for shu_plugin_sdk.template_cli."""

from __future__ import annotations

from pathlib import Path

import pytest

from shu_plugin_sdk.template_cli import copy_cookiecutter_template, main


def test_copy_cookiecutter_template_copies_expected_files(tmp_path: Path) -> None:
    destination = copy_cookiecutter_template("my_plugin", root=tmp_path)

    assert destination == (tmp_path / "my_plugin").resolve()
    assert (destination / "plugin.py").is_file()
    assert (destination / "manifest.py").is_file()
    assert (destination / "test_plugin.py").is_file()


def test_copy_cookiecutter_template_test_file_uses_relative_imports(tmp_path: Path) -> None:
    destination = copy_cookiecutter_template("my_plugin", root=tmp_path)
    test_file = (destination / "test_plugin.py").read_text()

    assert "from .manifest import PLUGIN_MANIFEST" in test_file
    assert "from .plugin import EchoPlugin" in test_file
    assert "from _cookiecutter.manifest" not in test_file
    assert "from _cookiecutter.plugin" not in test_file


def test_copy_cookiecutter_template_rejects_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        copy_cookiecutter_template("MyPlugin", root=tmp_path)


def test_copy_cookiecutter_template_ignores_shadowing_local_cookiecutter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cookiecutter = tmp_path / "_cookiecutter"
    fake_cookiecutter.mkdir()
    (fake_cookiecutter / "__init__.py").write_text("")
    (fake_cookiecutter / "plugin.py").write_text("# fake template")
    (fake_cookiecutter / "manifest.py").write_text("# fake template")
    (fake_cookiecutter / "test_plugin.py").write_text("# fake template")

    monkeypatch.chdir(tmp_path)
    destination = copy_cookiecutter_template("my_plugin", root=tmp_path / "plugins")

    assert "class EchoPlugin" in (destination / "plugin.py").read_text()


def test_copy_cookiecutter_template_existing_non_empty_requires_force(tmp_path: Path) -> None:
    existing = tmp_path / "my_plugin"
    existing.mkdir(parents=True)
    (existing / "notes.txt").write_text("hello")

    with pytest.raises(FileExistsError, match="--force"):
        copy_cookiecutter_template("my_plugin", root=tmp_path)


def test_copy_cookiecutter_template_existing_file_destination_fails(tmp_path: Path) -> None:
    file_destination = tmp_path / "my_plugin"
    file_destination.write_text("not a directory")

    with pytest.raises(FileExistsError, match="not a directory"):
        copy_cookiecutter_template("my_plugin", root=tmp_path)


def test_copy_cookiecutter_template_force_allows_existing_non_empty(tmp_path: Path) -> None:
    existing = tmp_path / "my_plugin"
    existing.mkdir(parents=True)
    (existing / "notes.txt").write_text("hello")

    destination = copy_cookiecutter_template("my_plugin", root=tmp_path, force=True)
    assert destination == existing.resolve()
    assert (destination / "plugin.py").is_file()


def test_cli_main_copies_to_default_plugins_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = main(["my_plugin", "--quiet"])

    assert exit_code == 0
    assert (tmp_path / "plugins" / "my_plugin" / "plugin.py").is_file()


def test_cli_main_exits_non_zero_on_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["BadName", "--root", str(tmp_path), "--quiet"])
    assert exc_info.value.code == 1
