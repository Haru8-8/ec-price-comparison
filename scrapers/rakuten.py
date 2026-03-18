import requests
from bs4 import BeautifulSoup
import re
import json
from bs4 import XMLParsedAsHTMLWarning
import warnings
import time
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .base import BaseScraper


class RakutenScraper(BaseScraper):

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        }

    def get_site_name(self) -> str:
        return 'Rakuten'

    def search(self, keyword: str) -> list[dict]:
        """楽天市場で検索し、商品情報のリストを返す"""
        return self.search_and_parse(keyword)

    # -------------------------
    # 検索
    # -------------------------

    def search_and_parse(self, keyword, pages=1):
        """楽天市場で検索し、商品URLと簡易情報のリストを返す"""
        items = []

        for page in range(1, pages + 1):
            search_url = f"https://search.rakuten.co.jp/search/mall/{keyword}/?p={page}"
            
            try:
                response = requests.get(search_url, headers=self.headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")

                search_items = soup.select("div.searchresultitem[data-card-type='item']")
                if not search_items:
                    print(f"ページ {page} に商品が見つかりませんでした。")
                    break # 商品がなくなったらループを抜ける

                for item in search_items:
                    link_el = item.select_one("a[data-link=item]")
                    if not link_el:
                        continue

                    url = link_el.get("href")
                    name = link_el.text.strip()

                    price_el = item.select_one('div[class*="price--"]')
                    if not price_el:
                        price_el = item.select_one('div.price--3zUvK')
                    price = int(re.sub(r'\D', '', price_el.text)) if price_el else 0

                    shipping_fee = 0
                    shipping_fee_el = item.select_one('span[class*="free-shipping-label--"]')
                    if not shipping_fee_el:
                        shipping_fee_el = item.select_one('span[class*="paid-shipping-wrapper--"] span')
                    if shipping_fee_el:
                        shipping_fee = self.parse_shipping_fee(shipping_fee_el.get_text(strip=True))

                    shop_name = item.select_one('div.merchant a').get_text(strip=True)

                    points = 0
                    points_rate = 0.0
                    points_el = item.select_one('div[class*="points--"] span')
                    if points_el:
                        points, points_rate = self.parse_rakuten_points(points_el.get_text(strip=True))

                    stock_status = "unknown"
                    shipping_el = item.select_one('div.shipping')
                    if shipping_el:
                        stock_status = self.classify_stock(shipping_el.get_text(strip=True))

                    image_el = item.select_one('div[class*="image-wrapper--"] a img')
                    image_url = image_el.get('src') if image_el else ""

                    items.append({
                        "name": name,
                        "product_url": url,
                        "price": price,
                        "shipping": shipping_fee,
                        "points": points,
                        "points_rate": points_rate,
                        "seller": shop_name,
                        "stock_status": stock_status,
                        "image_url": image_url,
                        "jan": None,      # fetch_rakuten_details で補完
                        "quantity": 1,    # main.py の extract_quantity で補完
                        "unit_price": 0,  # main.py で計算
                    })

                print(f"楽天 ページ {page} 取得完了 (現在 {len(items)} 件)")

                # 複数ページ取得時はサーバー負荷軽減のため待機
                if page < pages:
                    time.sleep(1.5)

            except Exception as e:
                print(f"楽天 検索エラー (ページ {page}): {e}")
                break # エラー発生時はそこまでの結果を返す

        return items

    # -------------------------
    # 詳細取得
    # -------------------------

    def fetch_rakuten_details(self, url):
        try:
            with requests.Session() as s:
                s.headers.update(self.headers)
                response = s.get(url, timeout=(5, 20), allow_redirects=True)
                response.raise_for_status()
                html = response.text
            soup = BeautifulSoup(html, "lxml")
            script_tag = soup.select_one("#item-page-app-data")
            if not script_tag:
                return None

            json_data = json.loads(script_tag.string)

            pre_tax_prices = self._find_key(json_data, "preTaxPrice")
            pre_tax_price = pre_tax_prices[0] if pre_tax_prices else 0

            shop_ids = self._find_key(json_data, "shopId")
            shop_id = shop_ids[0] if shop_ids else ""

            item_ids = self._find_key(json_data, "itemId")
            item_id = item_ids[0] if item_ids else ""
            site_item_id = f"{shop_id}_{item_id}"

            rev_avgs = self._find_key(json_data, 'itemReviewRating')
            rev_avg = rev_avgs[0] if rev_avgs else 0.0

            rev_counts = self._find_key(json_data, 'itemReviewCount')
            rev_count = rev_counts[0] if rev_counts else 0

            is_deals = self._find_key(json_data, 'superDeal')
            is_deal = 1 if (is_deals[0] if is_deals else False) else 0

            deal_rate = self._extract_deal_rate(json_data)

            shipping_el = soup.select_one("td[irc='DeliveryMethod']")
            free_threshold = self._parse_free_shipping_threshold(shipping_el.get_text()) if shipping_el else 0

            return {
                "jan": self._extract_jan(html),
                "shop_id": shop_id,
                "site_item_id": site_item_id,
                "pre_tax_price": pre_tax_price,
                "is_deal": is_deal,
                "deal_rate": deal_rate if deal_rate > 0 else 1,
                "review_average": rev_avg,
                "review_count": rev_count,
                "free_threshold": free_threshold
            }
        except Exception as e:
            print(f"Error fetching details: {e}")
            return None

    def fetch_jan(self, url):
        """楽天の商品ページからJANコードを抽出する"""
        try:
            with requests.Session() as s:
                s.headers.update(self.headers)
                response = s.get(url, timeout=(5, 20), allow_redirects=True)
                response.raise_for_status()
                html = response.text
            soup = BeautifulSoup(html, "lxml")

            jan = self._extract_jan(html)
            if not jan:
                jan_label = soup.find(string=re.compile(r"JANコード|商品番号"))
                if jan_label:
                    parent_text = jan_label.parent.get_text()
                    digit_match = re.search(r'(\d{12,13})', parent_text)
                    if digit_match:
                        return digit_match.group(1)
            return jan
        except Exception as e:
            print(f"楽天詳細取得エラー: {e}")
            return None

    # -------------------------
    # ユーティリティ（staticmethod）
    # -------------------------

    @staticmethod
    def parse_shipping_fee(shipping_text):
        if "送料無料" in shipping_text:
            return 0
        match = re.search(r'(\d+)', shipping_text)
        return int(match.group(1)) if match else 0

    @staticmethod
    def parse_rakuten_points(point_text):
        points_match = re.search(r'([\d,]+)\s*ポイント', point_text)
        points = int(points_match.group(1).replace(',', '')) if points_match else 0

        rate_match = re.search(r'\((.*?)\)', point_text)
        rate_str = rate_match.group(1) if rate_match else "1倍"

        if 'ポイントバック' in rate_str:
            num = re.search(r'(\d+)', rate_str)
            rate = int(num.group(1)) if num else 1
        else:
            nums = re.findall(r'(\d+)倍', rate_str)
            rate = sum(int(n) for n in nums)

        return points, rate

    @staticmethod
    def classify_stock(shipping_text):
        out_of_stock_keywords = ["売り切れ", "品切れ", "予約販売", "現在販売しておりません"]
        in_stock_keywords = ["発送予定", "お届け", "在庫", "営業日", "出荷"]
        if any(k in shipping_text for k in out_of_stock_keywords):
            return "out_of_stock"
        if any(k in shipping_text for k in in_stock_keywords):
            return "in_stock"
        return "unknown"

    @staticmethod
    def _extract_jan(text):
        try:
            jan_match = re.search(r'\b([459]\d{12})\b', text)
            return jan_match.group(1) if jan_match else None
        except Exception as e:
            print(f"JAN取得エラー: {e}")
            return None

    def _extract_deal_rate(self, data):
        campaigns = self._find_key(data, 'pointCampaign')
        if campaigns:
            for c in campaigns:
                rate_list = self._find_key(c, 'rate')
                if rate_list and rate_list[0]:
                    return rate_list[0]
        return 0

    def _parse_free_shipping_threshold(self, html):
        match = re.search(r'([\d,]+)円以上で送料無料', html)
        return int(match.group(1).replace(',', '')) if match else 0

    def _find_key(self, data, target_key):
        results = []
        if isinstance(data, dict):
            for key, value in data.items():
                if key == target_key:
                    results.append(value)
                results.extend(self._find_key(value, target_key))
        elif isinstance(data, list):
            for item in data:
                results.extend(self._find_key(item, target_key))
        return results

    def parse_rakuten_json_data(self, soup):
        script_tag = soup.select_one("#item-page-app-data")
        if not script_tag:
            return 0, 0
        try:
            data = json.loads(script_tag.string)
            is_deal = data.get('superDeal', False)
            rate = 0
            if is_deal:
                rate = data.get('shopSuperDeal', {}).get('rate', 0)
            return is_deal, rate
        except Exception as e:
            print(f"JSON解析エラー: {e}")
            return False, 0