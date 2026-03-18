import sqlite3
from datetime import datetime
from services.normalize_to_gtin import normalize_to_gtin
from config import Config

# Config.validate()
params = {
    'db_path': Config.DB_NAME,
}

class DatabaseManager:
    def __init__(self, db_path=params['db_path']):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _initialize_db(self):
        """確定したスキーマでテーブルを作成し、初期データを投入する"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. sites
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_name TEXT NOT NULL UNIQUE
                )
            ''')

            # 2. products
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gtin TEXT UNIQUE,
                    model_number TEXT,
                    product_name TEXT NOT NULL
                )
            ''')

            # 3. site_products
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS site_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    site_id INTEGER,
                    site_item_id TEXT,  -- Amazon: ASIN / 楽天: shopId_itemId / Yahoo: code
                    product_url TEXT NOT NULL,
                    image_url TEXT,
                    last_scraped_at DATETIME,
                    FOREIGN KEY (product_id) REFERENCES products(id),
                    FOREIGN KEY (site_id) REFERENCES sites(id),
                    UNIQUE(site_id, site_item_id)
                )
            ''')

            # 4. price_history
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_product_id INTEGER,
                    price INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 1,
                    unit_price REAL,
                    shipping_fee INTEGER DEFAULT 0,
                    points INTEGER DEFAULT 0,
                    points_rate REAL,
                    is_deal INTEGER DEFAULT 0,
                    review_average REAL,
                    review_count INTEGER,
                    stock_status TEXT,
                    seller_name TEXT,
                    shop_id TEXT,
                    is_amazon_sold BOOLEAN,
                    is_amazon_shipping BOOLEAN,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (site_product_id) REFERENCES site_products(id)
                )
            ''')

            # 5. scheduled_keywords（手動登録キーワード）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL UNIQUE,
                    is_active INTEGER DEFAULT 1,
                    last_run_at DATETIME,
                    next_run_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
 
            # 6. search_history（GUI検索履歴）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    searched_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            for name in ['Amazon', 'Rakuten', 'Yahoo']:
                cursor.execute("INSERT OR IGNORE INTO sites (site_name) VALUES (?)", (name,))

            conn.commit()

    # -------------------------
    # 共通ユーティリティ
    # -------------------------

    def _get_or_create_product(self, cursor, gtin, model, name):
        """productsテーブルから商品を取得、なければ作成してidを返す"""
        if gtin:
            cursor.execute("SELECT id FROM products WHERE gtin = ?", (gtin,))
        elif model:
            cursor.execute("SELECT id FROM products WHERE model_number = ?", (model,))
        else:
            return None

        result = cursor.fetchone()
        if result:
            return result[0]

        cursor.execute('''
            INSERT INTO products (gtin, model_number, product_name) VALUES (?, ?, ?)
        ''', (gtin, model, name))
        return cursor.lastrowid

    def _get_or_create_site_product(self, cursor, product_id, site_id, site_item_id, product_url, image_url):
        """site_productsテーブルから取得、なければ作成してidを返す"""
        cursor.execute('''
            SELECT id FROM site_products WHERE site_id = ? AND site_item_id = ?
        ''', (site_id, site_item_id))
        result = cursor.fetchone()

        if result:
            site_product_id = result[0]
            cursor.execute('''
                UPDATE site_products
                SET last_scraped_at = ?, product_url = ?, image_url = ?
                WHERE id = ?
            ''', (datetime.now(), product_url, image_url, site_product_id))
        else:
            cursor.execute('''
                INSERT INTO site_products
                (product_id, site_id, site_item_id, product_url, image_url, last_scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (product_id, site_id, site_item_id, product_url, image_url, datetime.now()))
            site_product_id = cursor.lastrowid

        return site_product_id

    def _get_site_id(self, cursor, site_name):
        """サイト名からsite_idを返す"""
        cursor.execute("SELECT id FROM sites WHERE site_name = ?", (site_name,))
        return cursor.fetchone()[0]

    # -------------------------
    # サイト別upsert
    # -------------------------

    def upsert_amazon_data(self, data):
        """
        Amazonのスクレイピング結果をDBに反映する
        data: {
            raw_code, model, name, asin, product_url, image_url,
            seller, is_amazon_sold, is_amazon_shipping,
            price, quantity, unit_price, shipping, points, points_rate, stock_status
        }
        """
        gtin = normalize_to_gtin(data.get('raw_code'))

        with self._get_connection() as conn:
            cursor = conn.cursor()

            product_id = self._get_or_create_product(
                cursor, gtin, data.get('model'), data['name']
            )
            if not product_id:
                return None

            site_id = self._get_site_id(cursor, 'Amazon')
            site_product_id = self._get_or_create_site_product(
                cursor, product_id, site_id,
                data['asin'], data['product_url'], data.get('image_url')
            )

            cursor.execute('''
                INSERT INTO price_history
                (site_product_id, price, quantity, unit_price, shipping_fee, points, points_rate,
                 stock_status, seller_name, is_amazon_sold, is_amazon_shipping, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                site_product_id,
                data['price'], data['quantity'], data['unit_price'],
                data.get('shipping', 0), data.get('points', 0), data.get('points_rate', 0.0),
                data['stock_status'], data['seller'],
                data['is_amazon_sold'], data['is_amazon_shipping'], datetime.now().isoformat(),
            ))

            conn.commit()
            return product_id

    def upsert_rakuten_data(self, data):
        """
        楽天市場のデータをDBに反映する
        data: {
            jan, name, product_url, image_url, price, quantity, unit_price,
            shipping, points, points_rate, seller, stock_status, shop_id, site_item_id
        }
        """
        gtin = normalize_to_gtin(data.get('jan'))
        if not gtin:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()

            product_id = self._get_or_create_product(cursor, gtin, None, data['name'])
            site_id = self._get_site_id(cursor, 'Rakuten')
            site_product_id = self._get_or_create_site_product(
                cursor, product_id, site_id,
                data['site_item_id'], data['product_url'], data.get('image_url')
            )

            cursor.execute('''
                INSERT INTO price_history
                (site_product_id, price, quantity, unit_price, shipping_fee, points, points_rate,
                 is_deal, review_average, review_count, stock_status, seller_name, shop_id,
                 is_amazon_sold, is_amazon_shipping, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                site_product_id,
                data['price'], data['quantity'], data['unit_price'],
                data['shipping'], data['points'], data['points_rate'],
                data.get('is_deal', 0),
                data.get('review_average', 0), data.get('review_count', 0),
                data['stock_status'], data['seller'], data.get('shop_id'),
                False, False, datetime.now().isoformat(),
            ))

            conn.commit()
            return product_id

    def upsert_yahoo_data(self, data):
        """
        Yahoo!ショッピングのデータをDBに反映する
        data: {
            jan, name, product_url, image_url, price, quantity, unit_price,
            shipping, points, points_rate, seller, stock_status, site_item_id
        }
        """
        gtin = normalize_to_gtin(data.get('jan'))
        if not gtin:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 楽天パイプライン経由なら既存productが存在するはず
            product_id = self._get_or_create_product(cursor, gtin, None, data['name'])
            site_id = self._get_site_id(cursor, 'Yahoo')
            site_product_id = self._get_or_create_site_product(
                cursor, product_id, site_id,
                data['site_item_id'], data['product_url'], data.get('image_url')
            )

            # Yahoo!APIはレビュー・DEALフラグを提供しないためNULL/0で登録
            cursor.execute('''
                INSERT INTO price_history
                (site_product_id, price, quantity, unit_price, shipping_fee, points, points_rate,
                 is_deal, review_average, review_count, stock_status, seller_name,
                 is_amazon_sold, is_amazon_shipping, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                site_product_id,
                data['price'], data['quantity'], data['unit_price'],
                data.get('shipping', 0), data.get('points', 0), data.get('points_rate', 0.0),
                0,      # is_deal: Yahoo!APIでは取得不可
                None,   # review_average: Yahoo!APIでは取得不可
                None,   # review_count: Yahoo!APIでは取得不可
                data.get('stock_status', 'unknown'),
                data.get('seller', '不明'),
                False,  # is_amazon_sold: Yahoo!なので常にFalse
                False,  # is_amazon_shipping: Yahoo!なので常にFalse
                datetime.now().isoformat(),
            ))

            conn.commit()
            return product_id
        
    # -------------------------
    # スケジューラ関連
    # -------------------------
 
    def add_scheduled_keyword(self, keyword: str):
        """スケジュール巡回キーワードを追加する"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO scheduled_keywords (keyword, created_at) VALUES (?, ?)
            """, (keyword,datetime.now().isoformat(),))
            conn.commit()
 
    def remove_scheduled_keyword(self, keyword: str):
        """スケジュール巡回キーワードを削除する"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scheduled_keywords WHERE keyword = ?", (keyword,))
            conn.commit()
 
    def get_scheduled_keywords(self) -> list[dict]:
        """スケジュール巡回キーワード一覧を返す"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT keyword, is_active, last_run_at, next_run_at
                FROM scheduled_keywords
                ORDER BY created_at ASC
            """).fetchall()
            return [dict(r) for r in rows]
 
    def update_scheduled_keyword_run_time(self, keyword: str, last_run_at: str, next_run_at: str):
        """実行時刻を更新する"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_keywords
                SET last_run_at = ?, next_run_at = ?
                WHERE keyword = ?
            """, (last_run_at, next_run_at, keyword))
            conn.commit()
 
    def record_search_history(self, keyword: str):
        """GUI検索履歴を記録する"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO search_history (keyword, searched_at) VALUES (?, ?)
            """, (keyword, datetime.now().isoformat()))
            conn.commit()
 
    def get_frequent_keywords(self, days: int = 7, min_count: int = 3) -> list[str]:
        """
        過去N日間でmin_count回以上検索されたキーワードを返す。
        すでにscheduled_keywordsに登録済みのものは除外する。
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT keyword, COUNT(*) as cnt
                FROM search_history
                WHERE searched_at >= datetime('now', ?)
                  AND keyword NOT IN (SELECT keyword FROM scheduled_keywords)
                GROUP BY keyword
                HAVING cnt >= ?
                ORDER BY cnt DESC
            """, (f'-{days} days', min_count)).fetchall()
            return [r['keyword'] for r in rows]
 
    def get_all_scrape_keywords(self, history_days: int = 7, min_count: int = 3) -> list[str]:
        """
        スクレイピング対象キーワードを返す。
        手動登録キーワード（is_active=1）＋検索頻度の高いキーワードの合算。
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
 
            # 手動登録キーワード
            rows = cursor.execute("""
                SELECT keyword FROM scheduled_keywords WHERE is_active = 1
            """).fetchall()
            manual = [r['keyword'] for r in rows]
 
            # 検索頻度が高いキーワード（手動登録済みは除外済み）
            frequent = self.get_frequent_keywords(history_days, min_count)
 
            # 重複なしで結合
            all_keywords = list(dict.fromkeys(manual + frequent))
            return all_keywords