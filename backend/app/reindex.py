"""Run with the application stopped: python -m app.reindex --user-id demo."""

import argparse
import logging

from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.services.embeddings import reindex

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="停止服务后，为指定用户的成功文档重建向量索引")
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    settings = Settings()
    db = Database(settings.data_dir / "knowledge.db")
    try:
        db.initialize()
        count = reindex(db, settings, user_id=args.user_id)
    except AppError as exc:
        print(f"重建失败：{exc.code}：{exc.message}")
        return 1
    except Exception:
        logger.exception("Vector reindex failed")
        print("重建失败：内部错误，请查看日志")
        return 1
    print(f"重建完成：{count} 个片段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
