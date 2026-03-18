"""
scheduler.py - EC価格比較ツール 自動巡回スクリプト

launchdによって毎日指定時刻に起動され、スクレイピングを実行して終了する。
常駐プロセスではないため、実行中以外はメモリを消費しない。

起動方法:
    python scheduler.py                  # 手動実行（テスト用）
    launchctl load ~/Library/LaunchAgents/com.ectools.scheduler.plist  # launchd登録
"""

import logging
from pathlib import Path
from datetime import datetime

from config import Config
from db.db_manager import DatabaseManager
from main import bridge_rakuten_to_amazon_yahoo

# -------------------------
# ロギング設定
# -------------------------
LOG_PATH = Config.get_external_path("scheduler.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== 自動巡回スクリプト起動 ===")

    config = Config.get_scheduler_config()

    # enabled=False の場合は何もせず終了
    if not config.get('enabled', False):
        logger.info("自動巡回が無効のため終了します")
        return

    db = DatabaseManager()
    history_days = config.get('history_days', 7)
    history_min_count = config.get('history_min_count', 3)

    keywords = db.get_all_scrape_keywords(
        history_days=history_days,
        min_count=history_min_count
    )

    if not keywords:
        logger.info("巡回対象キーワードがありません。終了します。")
        return

    logger.info(f"巡回対象: {keywords}")

    for keyword in keywords:
        logger.info(f"処理中: {keyword}")
        try:
            bridge_rakuten_to_amazon_yahoo(keyword)

            # 次回実行時刻を記録
            run_time = config.get('run_time', '08:00')
            h, m = map(int, run_time.split(':'))
            from datetime import timedelta
            next_run = (datetime.now() + timedelta(days=1)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
            db.update_scheduled_keyword_run_time(
                keyword,
                datetime.now().isoformat(),
                next_run.isoformat()
            )
            logger.info(f"完了: {keyword} / 次回: {next_run.strftime('%Y/%m/%d %H:%M')}")

        except Exception as e:
            logger.error(f"エラー ({keyword}): {e}")

    logger.info("=== 自動巡回スクリプト終了 ===")


if __name__ == "__main__":
    main()