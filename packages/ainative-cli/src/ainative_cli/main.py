"""`ainative` CLI入口——`ainative new <project-name> --type <type>`一条命令生成项目骨架。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ainative_cli.scaffold import InvalidProjectNameError, ProjectAlreadyExistsError, scaffold_project
from ainative_cli.templates import TEMPLATES, get_template

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ainative", description="AI Native Framework CLI")
    subparsers = parser.add_subparsers(dest="command")

    new_parser = subparsers.add_parser("new", help="生成一个新项目骨架")
    new_parser.add_argument("project_name", help="新项目的名称（也会作为目录名，除非用--dir指定）")
    new_parser.add_argument(
        "--type", dest="project_type", default="minimal",
        choices=sorted(TEMPLATES), help="项目类型模板（默认minimal）",
    )
    new_parser.add_argument("--dir", dest="target_dir", default=None, help="目标目录（默认为当前目录下的<project_name>）")
    new_parser.add_argument("--force", action="store_true", help="目标目录已存在且非空时仍然覆盖写入")

    subparsers.add_parser("list-types", help="列出全部可用的项目类型")

    return parser


def _cmd_new(args: argparse.Namespace) -> int:
    template = get_template(args.project_type)
    target_dir = Path(args.target_dir) if args.target_dir else Path.cwd() / args.project_name

    try:
        written = scaffold_project(target_dir, args.project_name, template, force=args.force)
    except (ProjectAlreadyExistsError, InvalidProjectNameError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created '{args.project_name}' ({template.description}) in {target_dir}")
    for path in written:
        print(f"  {path.name}")
    print(f"\nNext steps:\n  cd {target_dir}\n  uv sync\n  uv run python main.py")
    return 0


def _cmd_list_types(_args: argparse.Namespace) -> int:
    for name in sorted(TEMPLATES):
        template = TEMPLATES[name]
        print(f"{name}\n  {template.description}\n  packages: {', '.join(template.packages)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        return _cmd_new(args)
    if args.command == "list-types":
        return _cmd_list_types(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
