import sqlite3
from datetime import datetime, timedelta

def calc_effective_price(price, shipping, points):
    return price + shipping - points

def get_price_history(product_id, days=30, db_path="ec_tools.db"):
    """
    指定商品の過去N日間の価格履歴をサイト別に返す
    戻り値: {
        'Amazon': [{'timestamp': datetime, 'unit_price': float}, ...],
        'Rakuten': [...],
    }
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    since = datetime.now() - timedelta(days=days)

    query = """
    SELECT
        s.site_name,
        MIN(ph.unit_price) as unit_price,
        DATE(ph.timestamp) as date
    FROM price_history ph
    JOIN site_products sp ON ph.site_product_id = sp.id
    JOIN sites s ON sp.site_id = s.id
    WHERE sp.product_id = ?
      AND ph.timestamp >= ?
    GROUP BY s.site_name, DATE(ph.timestamp)
    ORDER BY date ASC
    """

    rows = cur.execute(query, (product_id, since.isoformat())).fetchall()
    conn.close()

    history = {}
    for r in rows:
        site = r['site_name']
        if site not in history:
            history[site] = []
        history[site].append({
            'timestamp': datetime.fromisoformat(r['date']),
            'unit_price': r['unit_price']
        })

    return history

def get_price_comparison(product_id, db_path="ec_tools.db"):

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
    SELECT
        s.site_name,
        sp.product_url,  -- 商品ページへのリンク
        sp.image_url,    -- 画像URL
        ph.price,
        ph.shipping_fee,
        ph.points,
        ph.quantity,
        ph.unit_price,
        ph.review_average,
        ph.review_count,
        ph.is_deal,
        ph.seller_name,
        ph.timestamp
    FROM price_history ph
    JOIN site_products sp ON ph.site_product_id = sp.id
    JOIN sites s ON sp.site_id = s.id
    WHERE ph.id IN (
        -- ここで「各サイト(site_id) × 商品(product_id) ごとの最安値」を特定する
        SELECT sub.id FROM (
            SELECT ph2.id, ROW_NUMBER() OVER (
                PARTITION BY sp2.site_id 
                ORDER BY ph2.unit_price ASC
            ) as rn
            FROM price_history ph2
            JOIN site_products sp2 ON ph2.site_product_id = sp2.id
            WHERE sp2.product_id = ?
        ) sub WHERE sub.rn = 1
    )
    """

    rows = cur.execute(query, (product_id,)).fetchall()
    conn.close()

    results = []

    for r in rows:

        results.append({
            "site": r["site_name"],
            "price": r["price"],
            "product_url": r["product_url"],
            "image_url": r["image_url"],
            "shipping": r["shipping_fee"],
            "points": r["points"],
            "quantity": r["quantity"],
            "unit_price": r["unit_price"],
            "review_avg": r["review_average"],
            "review_count": r["review_count"],
            "is_deal": bool(r["is_deal"]),
            "seller": r["seller_name"],
            "timestamp": r["timestamp"]
        })

    if not results:
        return None

    best = min(results, key=lambda x: x["unit_price"])

    return {
        "best_site": best["site"],
        "best_unit_price": best["unit_price"],
        "prices": results
    }

# --- 動作確認用 ---
if __name__ == "__main__":
    # テストとしてID: 1で実行してみる
    data = get_price_comparison(8)
    if data:
        print(f"最安サイト: {data['best_site']} (単価: {data['best_unit_price']})")
        for p in data['prices']:
            print(f"--- {p['site']} ---")
            print(f"価格: {p['price']}, 送料: {p['shipping']}, ポイント: {p['points']}, 評価: {p['review_avg']} ({p['review_count']}件)")
    else:
        print("データが見つかりませんでした。")