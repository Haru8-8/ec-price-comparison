import os
import sys
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from typing import List

# デフォルトのスケジューラ設定
DEFAULT_SCHEDULER_CONFIG = {
    "enabled": False,
    "run_time": "08:00",       # 実行時刻（HH:MM形式）
    "history_days": 7,          # 検索履歴の参照期間（日）
    "history_min_count": 3,     # 頻度の閾値（回以上で巡回対象）
    "max_pages": 1,
}


class Config:
    @staticmethod
    def get_external_path(relative_path):
        """
        .app の「外側」（ユーザーに見える場所）のパスを取得
        config.json, ec_tools.db, scheduler.log 用
        """
        if getattr(sys, 'frozen', False):
            # 実行ファイルから遡って .app の親ディレクトリ（Contentsのさらに上）を探す
            # p = os.path.abspath(sys.executable)
            # while p != "/":
            #     if p.endswith(".app"):
            #         return os.path.join(os.path.dirname(p), relative_path)
            #     p = os.path.dirname(p)
            # return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), relative_path)
            executable_path = os.path.abspath(sys.executable)
            
            # macOS特有の .app 構造の中にいる場合、3階層上に上がって .app の横を指す
            if ".app/Contents/MacOS" in executable_path:
                app_bundle_path = os.path.dirname(os.path.dirname(os.path.dirname(executable_path)))
                return os.path.join(os.path.dirname(app_bundle_path), relative_path)
            
            # それ以外（exeなど）の場合は実行ファイルの横
            return os.path.join(os.path.dirname(executable_path), relative_path)
        # 開発環境（スクリプト実行）
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
    
    @staticmethod
    def get_internal_resource_path(relative_path):
        """
        .app の「内部」（PyInstallerの一時展開先）のパスを取得
        .env (同梱する場合), アイコン, デフォルト設定用
        """
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, relative_path)
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

    # --- 各種パスの定義 ---

    # 1. .env は「内部」に同梱して隠蔽する（ビルド時に --add-data  (".env:.") する前提）
    ENV_PATH = get_external_path(".env")

    # .env ファイルが存在する場合のみ読み込む
    if os.path.exists(ENV_PATH):
        load_dotenv(dotenv_path=ENV_PATH)

    # 2. config.json は「外側」に置いてユーザーが編集・確認できるようにする
    CONFIG_JSON_PATH = get_external_path("config.json")

    # 3. launchd関連（これはユーザーディレクトリ固定なので変更なし）
    PLIST_NAME = "com.ectools.scheduler.plist"
    PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / PLIST_NAME

    # 4. ログファイル（外側に置くことで、ユーザーが不具合時に確認できる）
    LOG_PATH = get_external_path("scheduler.log")

    # 5. YAHOO_CLIENT_ID の取得と判定
    _raw_yahoo_id = os.getenv("YAHOO_CLIENT_ID", "")
    # 初期値（your_id_here）のままか、空なら None とみなす
    if _raw_yahoo_id == "your_id_here" or not _raw_yahoo_id.strip():
        YAHOO_CLIENT_ID = None
    else:
        YAHOO_CLIENT_ID = _raw_yahoo_id

    # 6. DB_NAME の取得と判定
    _raw_db_name = os.getenv("DB_NAME", "")
    # 初期値のまま、あるいは空ならデフォルトの "ec_tools.db" を使う
    if _raw_db_name == "your_favorite_db_name" or not _raw_db_name.strip():
        DB_NAME = get_external_path("ec_tools.db")
    else:
        DB_NAME = get_external_path(_raw_db_name)

    @classmethod
    def validate(cls):
        """必須の設定が読み込まれているかチェック"""
        missing = []
        if not cls.YAHOO_CLIENT_ID:
            missing.append("YAHOO_CLIENT_ID")
        if missing:
            raise ValueError(f"設定エラー: 以下の環境変数が不足しています: {', '.join(missing)}")
   


    # -------------------------
    # config.json の読み書き
    # -------------------------
 
    @classmethod
    def _load_json(cls) -> dict:
        try:
            with open(cls.CONFIG_JSON_PATH, encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            print(f"警告: {cls.CONFIG_JSON_PATH} の解析に失敗しました。デフォルト値を使用します。")
            return {}
 
    @classmethod
    def _save_json(cls, data: dict):
        with open(cls.CONFIG_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
 
    @classmethod
    def get_scheduler_config(cls) -> dict:
        """スケジューラ設定を取得する。未設定項目はデフォルト値で補完する"""
        data = cls._load_json()
        config = DEFAULT_SCHEDULER_CONFIG.copy()
        config.update(data.get('scheduler', {}))
        return config
 
    @classmethod
    def save_scheduler_config(cls, scheduler_config: dict):
        """スケジューラ設定を保存し、plistを更新する"""
        data = cls._load_json()
        data['scheduler'] = scheduler_config
        cls._save_json(data)
        cls._update_plist(scheduler_config)

    # -------------------------
    # launchd plist の管理
    # -------------------------
 
    @classmethod
    def _update_plist(cls, scheduler_config: dict):
        """
        plistを現在の設定で書き換えてlaunchdにreloadする。
        plistが存在しない場合は何もしない（初回セットアップ前）。
        """
        if not cls.PLIST_PATH.exists():
            return
 
        run_time = scheduler_config.get('run_time', '08:00')
        try:
            hour, minute = map(int, run_time.split(':'))
        except ValueError:
            print(f"警告: run_timeの形式が不正です: {run_time}")
            return
 
        # plistを読み込んでStartCalendarIntervalを更新
        try:
            content = cls.PLIST_PATH.read_text(encoding='utf-8')
 
            # Hour・Minuteを置き換え（簡易的な文字列置換）
            import re
            content = re.sub(
                r'(<key>Hour</key>\s*<integer>)\d+(</integer>)',
                rf'\g<1>{hour}\2',
                content
            )
            content = re.sub(
                r'(<key>Minute</key>\s*<integer>)\d+(</integer>)',
                rf'\g<1>{minute}\2',
                content
            )
            cls.PLIST_PATH.write_text(content, encoding='utf-8')
 
            # launchdにreload
            cls._reload_launchd()
        except Exception as e:
            print(f"plist更新エラー: {e}")
 
    @classmethod
    def _reload_launchd(cls):
        """launchdのジョブをunload→loadして設定を反映する"""
        label = cls.PLIST_NAME.replace('.plist', '')
        try:
            subprocess.run(
                ['launchctl', 'unload', str(cls.PLIST_PATH)],
                capture_output=True
            )
            subprocess.run(
                ['launchctl', 'load', str(cls.PLIST_PATH)],
                capture_output=True,
                check=True
            )
            print(f"launchd: {label} を再読み込みしました")
        except subprocess.CalledProcessError as e:
            print(f"launchd reload エラー: {e}")
        except FileNotFoundError:
            print("launchctl が見つかりません（macOS以外の環境）")
 
    @classmethod
    def setup_launchd(cls, exec_args: List, working_dir: str):
        """
        plistを生成してLaunchAgentsに配置し、launchdに登録する。
        GUIのセットアップ画面から1回だけ呼ぶ。
 
        Args:
            exec_args: venv内のpythonのフルパス（例: /path/to/.venv/bin/python）
            working_dir: プロジェクトのルートディレクトリのフルパス
        """
        # リストの中身を <string>タグで囲んで連結する
        config = cls.get_scheduler_config()
        run_time = config.get('run_time', '08:00')
        try:
            hour, minute = map(int, run_time.split(':'))
        except ValueError:
            hour, minute = 8, 0
        args_str = "\n".join([f"        <string>{arg}</string>" for arg in exec_args])
 
        plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ectools.scheduler</string>
 
    <key>ProgramArguments</key>
    <array>
{args_str}
    </array>
 
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
 
    <key>WorkingDirectory</key>
    <string>{working_dir}</string>
 
    <key>StandardOutPath</key>
    <string>{working_dir}/scheduler.log</string>
 
    <key>StandardErrorPath</key>
    <string>{working_dir}/scheduler_error.log</string>
</dict>
</plist>'''
 
        cls.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.PLIST_PATH.write_text(plist_content, encoding='utf-8')
        cls._reload_launchd()
        print(f"launchd登録完了: {cls.PLIST_PATH}")
 
    @classmethod
    def remove_launchd(cls):
        """launchdからジョブを削除する"""
        if not cls.PLIST_PATH.exists():
            return
        try:
            subprocess.run(
                ['launchctl', 'unload', str(cls.PLIST_PATH)],
                capture_output=True
            )
            cls.PLIST_PATH.unlink()
            print("launchd登録を解除しました")
        except Exception as e:
            print(f"launchd解除エラー: {e}")
 
    @classmethod
    def is_launchd_registered(cls) -> bool:
        """plistが登録済みかどうかを返す"""
        return cls.PLIST_PATH.exists()

    # -------------------------
    # APIキーのvalidation
    # -------------------------
 
    @classmethod
    def validate_api_key(cls):
        """APIキーが有効（存在）するかチェック"""
        return cls.YAHOO_CLIENT_ID is not None