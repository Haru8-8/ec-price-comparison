# main.py
import re
import html as html_module

from scrapers.amazon import AmazonScraper
from scrapers.rakuten import RakutenScraper
from scrapers.yahoo import YahooScraper
from db.db_manager import DatabaseManager
from services.price_comparison import get_price_comparison
from config import Config

# -------------------------
# 設定
# -------------------------

params = {
    "YAHOO_APP_ID": Config.YAHOO_CLIENT_ID,
}

# -------------------------
# ユーティリティ
# -------------------------

def extract_volume(name):
    """
    商品名から容量・重量を抽出してml/g換算で返す。
    取得できない場合はNoneを返す。
    """
    name = html_module.unescape(name)
    name = name.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    match = re.search(
        r'(\d+(?:\.\d+)?)\s*(ml|mL|ｍｌ|ミリリットル|g|ｇ|グラム|kg|ｋｇ|キログラム|L|ｌ|リットル)',
        name, re.IGNORECASE
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if 'kg' in unit or 'キログラム' in unit:
        value *= 1000
    elif unit in ('l', 'ｌ', 'リットル'):
        value *= 1000
    return value

def is_same_product(name1, name2):
    """
    2つの商品名が概ね同一商品かチェックする。
    Jaccard係数 + 容量チェックの2段階で判定する。
    """
    NOISE_WORDS = {
        '1種類を選べる', '種類を選べる', 'ケース販売', '送料無料', '公式',
        'あす楽', '楽天1位', 'ポイントバック', '超特大', '大容量', '業務用',
        '詰め替え', '詰替', 'つめかえ', 'まとめ買い', 'セット販売',
    }
 
    def to_words(n):
        n = html_module.unescape(n)
        n = re.sub(r'[【】\[\]（）\(\)\!\?★☆&％%]', ' ', n)
        words = set(re.sub(r'\s+', ' ', n).lower().split())
        return {w for w in words if w not in NOISE_WORDS and len(w) > 1}
 
    set1 = to_words(name1)
    set2 = to_words(name2)
    if not set1 or not set2:
        return False
 
    # チェック1: Jaccard係数
    score = len(set1 & set2) / min(len(set1), len(set2))
    if score < 0.15:
        return False
 
    # チェック2: 容量チェック（両方から取れた場合のみ比較）
    vol1 = extract_volume(name1)
    vol2 = extract_volume(name2)
    if vol1 and vol2:
        ratio = max(vol1, vol2) / min(vol1, vol2)
        if ratio >= 2.0:
            return False
 
    return True

def extract_quantity(name):
    """
    商品名から購入単位（セット数・袋数・本数）を抽出する。
    内容量（粒数・枚数・カプセル数）は除外する。
    """
    name = html_module.unescape(name)

    # 内容量を示す単語（これらの直前の数字は除外）
    # 例: 「57個入」「60カプセル」「48枚」は内容量なので除外
    CONTENT_UNITS = r'(?:粒|錠|カプセル|枚|包|食分|回分|ml|mL|g|kg|L)'

    patterns = [
        # 「2袋セット」「3袋入」など袋単位のセット
        r'(\d+)\s*袋(?:セット|入|組)',
        # 「3本セット」「2本入」など本単位のセット
        r'(\d+)\s*本(?:セット|入|組)',
        # 「5個セット」「3個ケース」（「個入」は内容量の可能性があるため除外）
        r'(\d+)\s*個(?:セット|ケース|組)',
        # 「5点セット」
        r'(\d+)\s*点セット',
        # 「×3セット」「x2セット」のようにセットと明示されているもの
        r'[x×]\s*(\d+)\s*セット',
        # 「(2袋セット)」のように括弧内に明示されているもの
        r'[（(]\s*(\d+)\s*袋',
        r'[（(]\s*(\d+)\s*本',
        r'[（(]\s*(\d+)\s*個(?:セット|ケース)',
    ]

    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            qty = int(match.group(1))
            # 念のため異常値を除外（100以上は内容量の可能性が高い）
            if qty <= 99:
                return qty

    return 1

def calc_unit_price(price, shipping, points, quantity):
    """実質単価を計算する"""
    return (price + shipping - points) / max(quantity, 1)


# -------------------------
# メインパイプライン
# -------------------------

def bridge_rakuten_to_amazon_yahoo(keyword, progress_callback=None, max_pages=1):
    """
    楽天を起点に検索し、JANコードを使ってAmazon・Yahoo!に横断する
    メインパイプライン。
    """
    rakuten = RakutenScraper()
    amazon = AmazonScraper()
    yahoo = YahooScraper(app_id=params['YAHOO_APP_ID'])
    db = DatabaseManager()

    print(f"楽天で検索中: {keyword} (最大 {max_pages} ページ)")
    rk_items = rakuten.search_and_parse(keyword, pages=max_pages)

    target_items = rk_items
    total = len(target_items)

    for i, rk_item in enumerate(target_items):
        # 進捗コールバック
        if progress_callback:
            progress_callback(i, total, rk_item['name'][:25])
            
        # ふるさと納税商品はJANが取れないのでスキップ
        if 'ふるさと納税' in rk_item['name']:
            print(f"[スキップ] ふるさと納税商品: {rk_item['name'][:40]}")
            continue

        print(f"\n--- 楽天商品解析: {rk_item['name'][:40]}... ---")

        # 1. 楽天詳細ページからJAN・ポイント等を補完
        details = rakuten.fetch_rakuten_details(rk_item['product_url'])
        if details:
            points = int(details['pre_tax_price'] * (details['deal_rate'] / 100))
            final_shipping = rk_item['shipping']
            if details['free_threshold'] > 0 and rk_item['price'] >= details['free_threshold']:
                final_shipping = 0
            rk_item.update({
                'jan': details['jan'],
                'shop_id': details['shop_id'],
                'points': points,
                'points_rate': details['deal_rate'],
                'shipping': final_shipping,
                'is_deal': details['is_deal'],
                'review_average': details['review_average'],
                'review_count': details['review_count'],
                'site_item_id': details['site_item_id']
            })

        if not rk_item.get('jan'):
            print("JANコードが取得できなかったためスキップ")
            continue

        # 2. 単価計算
        rk_qty = extract_quantity(rk_item['name'])
        rk_item['quantity'] = rk_qty
        rk_item['unit_price'] = calc_unit_price(
            rk_item['price'], rk_item['shipping'], rk_item['points'], rk_qty
        )

        # 3. 楽天をDBに保存（product_idが確定）
        product_id = db.upsert_rakuten_data(rk_item)
        print(f"楽天保存完了 (JAN: {rk_item['jan']})")

        jan = rk_item['jan']

        # 4. Amazon連携
        _bridge_amazon(amazon, db, rk_item, jan)

        # 5. Yahoo!連携
        _bridge_yahoo(yahoo, db, rk_item, jan, product_id)

        # 6. 比較結果を表示
        result = get_price_comparison(product_id)
        if result:
            print(f"=== 価格比較 (最安: {result['best_site']} ¥{result['best_unit_price']:.0f}/個) ===")

    print("\n全件処理完了！")

def _bridge_amazon(amazon: AmazonScraper, db: DatabaseManager, rk_item: dict, jan: str):
    """JANコードを使ってAmazonのデータを取得・保存する"""
    asin = amazon.find_asin_by_jan(jan)
    if not asin:
        print("Amazonで該当商品が見つかりませんでした")
        return

    az_url = f"https://www.amazon.co.jp/dp/{asin}"
    az_data = amazon.fetch_detail(az_url)

    if "error" in az_data:
        print(f"Amazon詳細取得エラー: {az_data['error']}")
        return

    if not is_same_product(rk_item['name'], az_data['name']):
        print(f"【警告】商品名不一致のためAmazonをスキップ")
        print(f"  楽天: {rk_item['name'][:35]}")
        print(f"  Amazon: {az_data['name'][:35]}")
        return
    
    # 価格が取れなかった場合はスキップ（DBに¥0データを入れない）
    if az_data.get('price', 0) == 0:
        print(f"【警告】価格が取得できなかったためAmazonをスキップ (ASIN: {asin})")
        return False

    az_qty = extract_quantity(az_data['name'])
    az_data['quantity'] = az_qty
    az_data['unit_price'] = calc_unit_price(
        az_data['price'], az_data['shipping'], az_data['points'], az_qty
    )
    az_data['asin'] = asin
    az_data['raw_code'] = jan

    db.upsert_amazon_data(az_data)
    print(f"Amazon保存完了 (ASIN: {asin})")


def _bridge_yahoo(yahoo: YahooScraper, db: DatabaseManager, rk_item: dict, jan: str, product_id: int):
    """JANコードを使ってYahoo!のデータを取得・保存する"""
    yahoo_items = yahoo.search_by_jan(jan)
    if not yahoo_items:
        print("Yahoo!で該当商品が見つかりませんでした")
        return
    
    for item in yahoo_items:
        # JANが一致しないものは弾く
        if item.get('jan') and item['jan'] != jan:
            continue
        if not is_same_product(rk_item['name'], item['name']):
            continue

        qty = extract_quantity(item['name'])
        item['quantity'] = qty
        item['unit_price'] = calc_unit_price(
            item['price'], item['shipping'], item['points'], qty
        )
        item['jan'] = jan
        item['product_id'] = product_id
        db.upsert_yahoo_data(item)

    print(f"Yahoo!保存完了")


# -------------------------
# 旧関数（後方互換）
# -------------------------

def bridge_rakuten_to_amazon(keyword):
    """後方互換のためのラッパー。bridge_rakuten_to_amazon_yahooを呼ぶ。"""
    bridge_rakuten_to_amazon_yahoo(keyword)


if __name__ == "__main__":
    bridge_rakuten_to_amazon_yahoo("エマール")