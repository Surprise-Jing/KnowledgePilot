"""Account administration for the local deployment. Run with the application stopped:

    python -m app.users list
    python -m app.users set-password demo            # prompts twice
    echo 'secret123' | python -m app.users set-password demo --password-stdin
    python -m app.users delete alice --yes

set-password is how users created before login existed (schema v7) get a password."""

import argparse
import getpass
import logging
import sys

from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.services import artifacts
from app.services import auth as service

logger = logging.getLogger(__name__)


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("新密码：")
    if getpass.getpass("再次输入：") != first:
        raise AppError(400, "password_mismatch", "两次输入的密码不一致")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KnowledgePilot 账号管理（请先停止服务）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出用户")
    set_password = commands.add_parser("set-password", help="设置或重置密码，并注销其所有会话")
    set_password.add_argument("username")
    set_password.add_argument("--password-stdin", action="store_true", help="从标准输入读取密码")
    delete = commands.add_parser("delete", help="删除用户及其全部知识库、文档和会话")
    delete.add_argument("username")
    delete.add_argument("--yes", action="store_true", help="确认删除（不可恢复）")
    args = parser.parse_args(argv)

    settings = Settings()
    db = Database(settings.data_dir / "knowledge.db")
    try:
        db.migrate()
        if args.command == "list":
            with db.connect() as connection:
                for user in service.list_users(connection):
                    state = "已设密码" if user["has_password"] else "未设密码"
                    print(f"{user['username'] or '(无用户名)'}\t{user['id']}\t{state}")
            return 0
        if args.command == "set-password":
            user = service.set_password(db, args.username, _read_password(args.password_stdin))
            print(f"已为 {user['username']} 设置密码，原有登录会话已注销")
            return 0
        if not args.yes:
            print("删除不可恢复，请加 --yes 确认")
            return 2
        removed = service.delete_user(
            db, args.username, artifacts_root=artifacts.artifacts_root(settings)
        )
        print(f"已删除用户 {args.username}" if removed else f"用户 {args.username} 不存在")
        return 0
    except AppError as exc:
        print(f"操作失败：{exc.message}")
        return 1
    except Exception:
        logger.exception("user administration failed")
        print("操作失败：内部错误，请查看日志")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
