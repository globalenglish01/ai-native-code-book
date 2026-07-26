"""`ainative` CLI入口——`ainative new <project-name> --type <type>`一条命令生成项目骨架。"""

# 让类型注解可以延迟解析，详见ainative_core里的详细解释，这里不再重复。
from __future__ import annotations

# argparse——Python标准库自带的"命令行参数解析"模块。写命令行工具时，
# 用户在终端里敲的`ainative new my_app --type minimal`这一整行文字，
# 需要被拆解成程序能理解的结构化数据（"子命令是new"、"project_name是
# my_app"、"project_type是minimal"），argparse就是专门做这件事的标准
# 工具，不需要自己手写字符串切分/解析逻辑。
import argparse

# sys——Python标准库，提供和"当前正在运行的Python解释器/程序本身"相关
# 的功能，比如下面用到的`sys.argv`（命令行传入的原始参数）、
# `sys.exit(...)`（结束程序并返回一个退出码）、`sys.stderr`（标准错误
# 输出流，专门用来打印错误信息，和`sys.stdout`正常输出分开）。
import sys

# Path——见scaffold.py里的详细解释：`pathlib`提供的路径对象，比手动
# 拼接字符串更安全、跨平台。
from pathlib import Path

from ainative_cli.scaffold import InvalidProjectNameError, ProjectAlreadyExistsError, scaffold_project
from ainative_cli.templates import TEMPLATES, get_template

# 这一段是"平台兼容性"处理：`sys.platform == "win32"`判断当前是否运行
# 在Windows系统上；`hasattr(sys.stdout, "reconfigure")`判断"当前这个
# 标准输出对象是否具备`reconfigure`这个方法"（较老的Python版本或者
# 某些特殊的输出重定向场景下可能没有，用`hasattr`先探测一下，避免直接
# 调用一个不存在的方法导致程序崩溃）。两个条件都满足时，才调用
# `sys.stdout.reconfigure(encoding="utf-8")`把标准输出/标准错误输出的
# 字符编码强制设置成UTF-8——因为Windows终端的默认编码在某些语言/区域
# 设置下不是UTF-8，如果CLI要打印中文字符（比如后面`list-types`命令里
# 模板的中文描述），不做这个设置可能会在Windows上直接报编码错误崩溃
# 或者显示乱码。这段代码写在模块顶层（不在任何函数里面），意味着只要
# `import ainative_cli.main`这个模块，就会立刻执行一次。
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    # `argparse.ArgumentParser(...)`——创建一个"参数解析器"对象，是
    # 使用argparse的起点。`prog="ainative"`指定了帮助信息里显示的程序
    # 名字（比如`ainative --help`里看到的用法提示会用这个名字），
    # `description`是`--help`输出里最顶部的一句话说明。
    parser = argparse.ArgumentParser(prog="ainative", description="AI Native Framework CLI")
    # `add_subparsers(...)`——很多命令行工具（比如`git`）都是"一个主命令
    # 名 + 一个子命令"的结构，比如`git commit`、`git push`里的
    # `commit`/`push`就是"子命令"。`dest="command"`表示解析完成后，
    # 用户实际输入的子命令名会被存到`args.command`这个属性里，后面
    # `main()`函数就是靠读取`args.command`来判断该执行哪个子命令逻辑。
    subparsers = parser.add_subparsers(dest="command")

    # `add_parser("new", ...)`——注册一个名为"new"的子命令，`help`参数
    # 是`ainative --help`列出所有子命令时，"new"这一行对应显示的说明。
    new_parser = subparsers.add_parser("new", help="生成一个新项目骨架")
    # `add_argument("project_name", ...)`——给"new"这个子命令添加一个
    # "位置参数"（没有`--`前缀，靠出现的位置来识别，必须提供，比如
    # `ainative new my_app`里的"my_app"）。
    new_parser.add_argument("project_name", help="新项目的名称（也会作为目录名，除非用--dir指定）")
    # `add_argument("--type", ...)`——添加一个"可选参数"（带`--`前缀，
    # 用户输入时形如`--type minimal`）：
    # - `dest="project_type"`——指定解析结果存到`args.project_type`里
    #   （而不是默认根据`--type`自动生成的名字），这样代码里读取时
    #   属性名更贴近它的实际含义。
    # - `default="minimal"`——用户不传`--type`时，自动使用"minimal"。
    # - `choices=sorted(TEMPLATES)`——限定这个参数只能是`TEMPLATES`
    #   字典里注册过的模板名之一（`sorted(...)`让候选列表按字母顺序
    #   排列，`--help`里显示得整齐）；如果用户输入了一个不在列表里的
    #   值，argparse会自动报错并退出，不需要代码自己再写校验逻辑。
    new_parser.add_argument(
        "--type", dest="project_type", default="minimal",
        choices=sorted(TEMPLATES), help="项目类型模板（默认minimal）",
    )
    new_parser.add_argument("--dir", dest="target_dir", default=None, help="目标目录（默认为当前目录下的<project_name>）")
    # `action="store_true"`——这种可选参数不需要用户额外提供值，只要
    # 命令行里出现了`--force`这个词本身，解析结果里对应属性就自动是
    # `True`；用户完全不写`--force`，则该属性是`False`（相当于一个
    # 开关/旗标参数，而不是"需要跟一个值"的参数）。
    new_parser.add_argument("--force", action="store_true", help="目标目录已存在且非空时仍然覆盖写入")

    # 再注册第二个子命令"list-types"——它不需要接收任何额外参数，
    # 所以这里直接调用`add_parser`之后就没有继续`add_argument`，
    # 这个子命令对象本身也没有存成变量（因为后面用不到它）。
    subparsers.add_parser("list-types", help="列出全部可用的项目类型")

    return parser


def _cmd_new(args: argparse.Namespace) -> int:
    # `args: argparse.Namespace`——`parser.parse_args(...)`解析完命令行
    # 之后返回的正是一个`Namespace`对象，它的每个属性名对应前面
    # `add_argument`里`dest=`（或参数名本身）指定的名字，比如
    # `args.project_name`、`args.project_type`。
    template = get_template(args.project_type)
    # 三元条件表达式：如果用户传了`--dir`（`args.target_dir`不是None），
    # 就用`Path(args.target_dir)`把这个字符串转成路径对象；否则默认用
    # "当前工作目录下、以项目名命名的子目录"作为目标位置。
    # `Path.cwd()`——获取程序运行时"当前所在的工作目录"（terminal所在
    # 的那个目录）。
    target_dir = Path(args.target_dir) if args.target_dir else Path.cwd() / args.project_name

    # `try`/`except`——尝试执行真正的脚手架生成逻辑；如果`scaffold_project`
    # 抛出了这两种"预期内、面向用户"的错误（项目已存在/项目名不合法），
    # 就不让程序直接崩溃、打印一大段技术性的Python堆栈跟踪吓到用户，
    # 而是打印一条简洁的"error: ..."提示，返回退出码1（终端里"1"通常
    # 约定表示"命令执行失败"，"0"表示"成功"，这是类Unix命令行工具的
    # 通用惯例）。
    try:
        written = scaffold_project(target_dir, args.project_name, template, force=args.force)
    except (ProjectAlreadyExistsError, InvalidProjectNameError) as exc:
        # `file=sys.stderr`——把这条错误信息打印到"标准错误输出"而不是
        # 默认的"标准输出"，这是命令行工具的通用惯例：正常结果打印到
        # stdout，错误/警告信息打印到stderr，这样用户或者上层脚本可以
        # 分别处理这两路输出（比如把正常结果重定向保存到文件，但错误
        # 信息仍然显示在终端上）。
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created '{args.project_name}' ({template.description}) in {target_dir}")
    # `for path in written`——`written`是`scaffold_project`返回的、
    # 实际生成的文件路径列表，逐个打印出每个文件的名字（`path.name`
    # 只取文件名本身，不带完整目录路径，比如打印"main.py"而不是
    # 一长串"D:\...\my_project\main.py"），让用户一眼看清生成了哪些文件。
    for path in written:
        print(f"  {path.name}")
    print(f"\nNext steps:\n  cd {target_dir}\n  uv sync\n  uv run python main.py")
    return 0


def _cmd_list_types(_args: argparse.Namespace) -> int:
    # 参数名前面加下划线`_args`——Python的命名惯例，表示"这个参数按照
    # 函数签名规范必须存在（因为`main()`里统一按同样的方式调用所有
    # 子命令处理函数），但函数体内其实用不到它"。这里"list-types"命令
    # 不需要任何用户输入的参数，所以整个函数体都没有读取`_args`。
    for name in sorted(TEMPLATES):
        template = TEMPLATES[name]
        print(f"{name}\n  {template.description}\n  packages: {', '.join(template.packages)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    # `argv: list[str] | None = None`——这个函数既可以被真正的命令行
    # 调用（不传参数，`argv`是None，下面`parse_args(None)`会自动去读
    # 真实的`sys.argv`），也可以在测试代码里直接传入一个模拟的参数
    # 列表（比如`main(["new", "my_app", "--type", "minimal"])`）来测试，
    # 不需要真的启动一个子进程、拼一整行命令行字符串。
    parser = _build_parser()
    # `parser.parse_args(argv)`——真正执行解析：如果`argv`是None，
    # argparse内部会自动改用`sys.argv[1:]`（真实命令行参数，去掉第0个
    # "程序自己的文件名"这一项）；否则就解析调用方传入的这份模拟参数
    # 列表。解析失败（比如必填参数缺失、`--type`传了一个不认识的值）
    # 时，argparse会自己打印错误信息并直接结束程序，不会把控制权交还
    # 给下面的代码。
    args = parser.parse_args(argv)

    # 根据解析出来的子命令名（`args.command`），分发到对应的处理函数。
    if args.command == "new":
        return _cmd_new(args)
    if args.command == "list-types":
        return _cmd_list_types(args)

    # 走到这里说明用户没有输入任何合法的子命令（比如只敲了`ainative`
    # 本身，什么子命令都没给），`parser.print_help()`打印出完整的用法
    # 提示（有哪些子命令、每个参数是什么意思），返回退出码1表示"这不是
    # 一次成功的调用"。
    parser.print_help()
    return 1


# `if __name__ == "__main__":`——Python的标准写法，判断"这个文件是不是
# 被直接运行的"（而不是被别的模块`import`进去当作库使用）。只有直接
# 运行这个文件时（比如`python main.py`），才会执行下面这一行；如果只是
# 被别的代码import，则不会自动跑起来。
if __name__ == "__main__":
    # `sys.exit(main())`——调用`main()`拿到一个整数退出码，再用
    # `sys.exit(...)`把这个整数交给操作系统，作为整个程序的最终退出
    # 状态（终端里可以用`echo $?`这类命令查看上一条命令的退出码，
    # 0表示成功，非0表示某种失败——脚本化调用这个CLI的场景可以据此
    # 判断本次调用是否成功）。
    sys.exit(main())
