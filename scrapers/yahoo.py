import requests
import re
import html as html_module

from .base import BaseScraper

# Yahoo!ショッピング 商品検索API v3
ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"


class YahooScraper(BaseScraper):

    def __init__(self, app_id: str):
        self.app_id = app_id

    def get_site_name(self) -> str:
        return 'Yahoo'

    def search(self, keyword: str) -> list[dict]:
        """キーワードで検索し、商品情報のリストを返す"""
        return self._fetch(query=keyword)

    def search_by_jan(self, jan: str) -> list[dict]:
        """JANコードで検索し、商品情報のリストを返す"""
        return self._fetch(jan_code=jan)

    # -------------------------
    # 内部処理
    # -------------------------

    def _fetch(self, query: str = None, jan_code: str = None, results: int = 20) -> list[dict]:
        """
        APIを叩いてレスポンスをパースする共通メソッド。
        query か jan_code のどちらかを指定する。
        """
        params = {
            'appid': self.app_id,
            'results': results,
            'sort': '-score',           # 関連度順
        }
        if query:
            params['query'] = query
        if jan_code:
            params['jan_code'] = jan_code

        try:
            response = requests.get(ENDPOINT, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Yahoo!ショッピングAPIエラー: {e}")
            return []

        hits = data.get('hits', [])
        if not hits:
            return []

        items = []
        for hit in hits:
            # 価格
            price = hit.get('price', 0)

            # 送料
            shipping_info = hit.get('shipping', {})
            shipping_code = shipping_info.get('code', 1)
            # code=1: 送料無料, code=2: 条件付き送料無料, code=3: 送料別
            shipping_fee = 0 if shipping_code == 1 else self._extract_shipping_cost(shipping_info)

            # ポイント
            point_info = hit.get('point', {})
            point_amount = point_info.get('amount', 0)
            # lyLimitedBonusAmount: PayPayポイント（期間限定）
            ly_bonus = point_info.get('lyLimitedBonusAmount', 0)
            total_points = point_amount + ly_bonus
            point_rate = point_info.get('times', 1)

            # 販売店
            seller_info = hit.get('seller', {})
            seller_name = html_module.unescape(seller_info.get('name', '不明'))

            # 在庫
            stock_status = self._parse_stock(hit.get('availability', 1))

            # JANコード
            jan = hit.get('janCode') or hit.get('jan_code')

            # 画像URL
            image_url = hit.get('image', {}).get('medium', '')

            # 商品URL
            product_url = hit.get('url', '')

            # 商品名
            name = html_module.unescape(hit.get('name', ''))

            # site_item_id
            site_item_id = hit.get('code', '')  # 例: "zozo_52582864"

            items.append({
                'name': name,
                'price': price,
                'shipping': shipping_fee,
                'points': total_points,
                'points_rate': float(point_rate),
                'seller': seller_name,
                'stock_status': stock_status,
                'jan': jan,
                'image_url': image_url,
                'product_url': product_url,
                'site_item_id': site_item_id,
                'quantity': 1,    # main.py の extract_quantity で補完
                'unit_price': 0,  # main.py で計算
            })

        return items

    def _extract_shipping_cost(self, shipping_info: dict) -> int:
        """送料情報から送料を抽出する"""
        name = shipping_info.get('name', '')
        match = re.search(r'([\d,]+)', name)
        return int(match.group(1).replace(',', '')) if match else 0

    def _parse_stock(self, availability: int) -> str:
        """
        Yahoo!ショッピングのavailabilityコードを在庫ステータスに変換する
        1: 在庫あり, 2: 残り僅か, 3: 予約受付中, 0: 在庫なし
        """
        mapping = {
            1: 'in_stock',
            2: 'limited',
            3: 'preorder',
            0: 'out_of_stock',
        }
        return mapping.get(availability, 'unknown')