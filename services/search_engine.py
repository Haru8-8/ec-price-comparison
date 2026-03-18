# search_engine.py
import sqlite3
from config import Config

config_params = {
    'db_path': Config.DB_NAME,
}

def search_products(keyword, db_path=config_params['db_path']):
    """
    キーワードまたはGTIN(JANコード)で商品を検索する関数
    取得済みサイト情報も合わせて返す。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # カラム名でアクセス可能にする
    cur = conn.cursor()

    # 1. ユーザー入力が13桁の数字ならGTIN検索、そうでなければキーワード検索
    if keyword.isdigit() and len(keyword) == 13:
        product_query = "SELECT * FROM products WHERE gtin = ?"
        params = (keyword,)
    else:
        # LIKE演算子で部分一致検索
        product_query = "SELECT * FROM products WHERE product_name LIKE ?"
        params = (f"%{keyword}%",)

    rows = cur.execute(product_query, params).fetchall()

    if not rows:
        conn.close()
        return []
 
    # 商品IDのリストを取得
    product_ids = [row['id'] for row in rows]

    # 取得済みサイトをまとめて1クエリで取得（N+1問題を回避）
    placeholders = ','.join('?' * len(product_ids))
    site_query = f"""
        SELECT sp.product_id, s.site_name
        FROM site_products sp
        JOIN sites s ON sp.site_id = s.id
        WHERE sp.product_id IN ({placeholders})
        GROUP BY sp.product_id, s.site_name
    """
    site_rows = cur.execute(site_query, product_ids).fetchall()
    conn.close()

    # product_id → サイト名セット のマップを構築
    site_map = {}
    for sr in site_rows:
        pid = sr['product_id']
        if pid not in site_map:
            site_map[pid] = set()
        site_map[pid].add(sr['site_name'])
    
    # 結果を組み立て
    results = []
    for row in rows:
        item = dict(row)
        item['sites'] = site_map.get(item['id'], set())
        results.append(item)
 
    return results

# --- 動作テスト用 ---
if __name__ == "__main__":
    test_keyword = "アタック" # またはJANコード
    items = search_products(test_keyword)
    for item in items:
        print(f"ID: {item['id']} | JAN: {item['gtin']} | NAME: {item['product_name']}  | SITES: {item['sites']}")