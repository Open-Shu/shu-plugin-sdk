"""CLI helpers for copying the bundled plugin template into a project."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Sequence

_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _template_dir() -> Path:
    """Return the on-disk path for the bundled ``_cookiecutter`` template."""
    template_path = Path(__file__).resolve().parent.parent / "_cookiecutter"
    if template_path.exists():
        return template_path

    raise RuntimeError(
        "Could not locate bundled _cookiecutter template files. "
        "Reinstall shu-plugin-sdk and try again."
    )


def copy_cookiecutter_template(
    plugin_name: str,
    *,
    root: str | Path = "plugins",
    force: bool = False,
) -> Path:
    """Copy the bundled template into ``<root>/<plugin_name>``.

    Args:
        plugin_name: Target plugin directory name. Must be snake_case.
        root: Parent directory where plugin folders are created.
        force: If True, allow overwriting files in an existing directory.

    Returns:
        The absolute path to the copied template directory.

    Raises:
        ValueError: If ``plugin_name`` is invalid.
        FileExistsError: If destination exists and is non-empty without ``force``.
    """
    if not _PLUGIN_NAME_PATTERN.match(plugin_name):
        raise ValueError(
            f"Invalid plugin name {plugin_name!r}. "
            "Use snake_case like 'my_plugin'."
        )

    destination = Path(root) / plugin_name
    if destination.exists() and not destination.is_dir():
        raise FileExistsError(
            f"Destination {destination} exists and is not a directory. "
            "Use a different plugin name or remove the file."
        )
    if destination.exists() and any(destination.iterdir()) and not force:
        raise FileExistsError(
            f"Destination {destination} already exists and is not empty. "
            "Use --force to overwrite."
        )

    destination.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    shutil.copytree(_template_dir(), destination, dirs_exist_ok=True, ignore=ignore)
    return destination.resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shu-plugin-template",
        description=(
            "Copy the bundled shu-plugin-sdk template into "
            "plugins/<plugin_name>."
        ),
    )
    parser.add_argument(
        "plugin_name",
        help="Plugin directory name (snake_case), e.g. my_plugin",
    )
    parser.add_argument(
        "--root",
        default="plugins",
        help="Parent directory for generated plugin folders (default: plugins)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files if the destination directory already exists.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress success output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``shu-plugin-template`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        destination = copy_cookiecutter_template(
            args.plugin_name,
            root=args.root,
            force=args.force,
        )
    except (RuntimeError, ValueError, FileExistsError) as exc:
        parser.exit(1, f"error: {exc}\n")

    if not args.quiet:
        print(f"Copied template to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
