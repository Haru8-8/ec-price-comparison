import requests
from bs4 import BeautifulSoup
import re
import time

from .base import BaseScraper


class AmazonScraper(BaseScraper):
    """
    AmazonSearchScraper と AmazonDetailScraper を統合したクラス。
    """

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept-Language": "ja-JP,ja;q=0.9"
        }

    def get_site_name(self) -> str:
        return 'Amazon'

    def search(self, keyword: str) -> list[dict]:
        """キーワードで検索し、ASIN・商品名・URLのリストを返す"""
        return self.get_search_results(keyword, pages=1)

    # -------------------------
    # 検索
    # -------------------------

    def get_search_results(self, keyword, pages=1):
        """検索結果からASIN・商品名・URLのリストを返す"""
        all_results = []
        try:
            for page in range(1, pages + 1):
                search_url = f"https://www.amazon.co.jp/s?k={keyword}&page={page}"
                response = requests.get(search_url, headers=self.headers, timeout=10)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                items = soup.select('div[data-asin][role="listitem"]')
                for item in items:
                    asin = item.get('data-asin')
                    if not asin:
                        continue
                    title_element = item.select_one('h2 span')
                    name = title_element.text.strip() if title_element else "不明"
                    url = f"https://www.amazon.co.jp/dp/{asin}"
                    all_results.append({"asin": asin, "name": name, "url": url})

                time.sleep(2)
        except Exception as e:
            print(f"検索エラー: {e}")
        return all_results

    def find_asin_by_jan(self, jan):
        """JANコードでAmazonを検索し、最初のASINを返す"""
        if not jan:
            return None
        search_url = f"https://www.amazon.co.jp/s?k={jan}"
        response = requests.get(search_url, headers=self.headers)
        soup = BeautifulSoup(response.text, "html.parser")
        first_item = soup.select_one('div[data-asin][role="listitem"]')
        return first_item.get('data-asin') if first_item else None

    # -------------------------
    # 詳細取得
    # -------------------------

    def fetch_detail(self, url):
        try:
            clean_url = url.split("?")[0]
            response = requests.get(clean_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            # ポイント・配送料
            points, points_rate, shipping_fee = self._fetch_amazon_details_extra(soup)

            # 価格
            price_div = soup.select_one("#corePriceDisplay_desktop_feature_div")
            price_text = price_div.select_one(".a-price-whole") if price_div else None
            price = self._extract_number(price_text.text) if price_text else 0

            # 価格が取れなかった場合はASINを使って出品者一覧APIから取得
            if price == 0:
                asin_from_url = clean_url.split("/dp/")[-1].split("/")[0]
                aod_price, aod_shipping = self._fetch_price_from_aod(asin_from_url)
                if aod_price > 0:
                    price = aod_price
                    shipping_fee = aod_shipping  # 通常ページの送料を上書き
                    print(f"AODから価格取得: ¥{price} 送料: ¥{shipping_fee}")

            # 販売元・発送元
            seller_name = "不明"
            is_amazon_shipping = False

            seller_element = soup.select_one("#sellerProfileTriggerId")
            if seller_element:
                seller_name = self._clean_text(seller_element.text)
            else:
                merchant_info = soup.select_one("#merchantInfoFeature_feature_div a")
                if merchant_info:
                    merchant_text = self._clean_text(merchant_info.text)
                    if "Amazon" in merchant_text:
                        seller_name = "Amazon.co.jp"
                    else:
                        seller_name = merchant_text.split("が販売")[0].strip()

            is_amazon_shipping = self._extract_shipping_info(soup)
            is_amazon_sold = "Amazon" in seller_name

            # 在庫
            stock_element = soup.select_one("#availability")
            stock_raw = self._clean_text(stock_element.text) if stock_element else ""

            # JANコード・型番
            details_text = soup.get_text()
            raw_code = None
            jan_match = re.search(r'\b([49]\d{12})\b', details_text)
            if jan_match:
                raw_code = jan_match.group(1)
            else:
                upc_match = re.search(r'\b(\d{12})\b', details_text)
                if upc_match:
                    raw_code = upc_match.group(1)

            model_number = "不明"
            model_match = re.search(r'(?:メーカー型番|商品モデル番号)\s*[:：]\s*([^\s\n]+)', details_text)
            if model_match:
                model_number = model_match.group(1).strip()

            # 商品名
            title_el = soup.select_one("#productTitle")
            product_name = title_el.text.strip() if title_el else "不明な商品"

            # 画像URL
            img_el = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
            actual_image_url = ""
            if img_el:
                actual_image_url = img_el.get("data-old-hires") or img_el.get("src")

            return {
                "price": price,
                "seller": seller_name,
                "is_amazon_sold": is_amazon_sold,
                "is_amazon_shipping": is_amazon_shipping,
                "stock_status": self._parse_stock_status(stock_raw),
                "raw_code": raw_code,
                "model": model_number,
                "product_url": clean_url,
                "name": product_name,
                "image_url": actual_image_url,
                "shipping": shipping_fee,
                "points": points,
                "points_rate": points_rate,
            }
        except Exception as e:
            return {"error": str(e)}

    # -------------------------
    # ユーティリティ
    # -------------------------

    def _fetch_amazon_details_extra(self, soup):
        point_element = soup.select_one("#points_feature_div span")
        points = 0
        points_rate = 0.0
        if point_element:
            point_text = point_element.get_text()
            points_match = re.search(r'(\d+)\s*(?:ポイント|pt)', point_text)
            points = int(points_match.group(1)) if points_match else 0
            rate_match = re.search(r'\((\d+)%\)', point_text)
            points_rate = float(rate_match.group(1)) if rate_match else 0.0

        shipping_fee = 0
        shipping_element = soup.select_one("#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE span")
        if shipping_element:
            shipping_text = shipping_element.get_text()
            if "無料" not in shipping_text:
                shipping_fee = self._extract_shipping(shipping_text)

        return points, points_rate, shipping_fee

    def _extract_shipping_info(self, soup):
        selectors = [
            "#icon-farm #DELIVERY_JP div a",
            "#DELIVERY_JP div a",
            "#fullfillerInfoFeature_feature_div",
            "#merchantInfoFeature_feature_div",
            "#tabular-buybox .tabular-buybox-row",
            "#merchant-info"
        ]
        for sel in selectors:
            element = soup.select_one(sel)
            if element and "Amazon" in self._clean_text(element.text):
                return True

        full_text = soup.get_text()
        if "出荷元" in full_text:
            match = re.search(r'出荷元[:：]\s*(.*?)\n', full_text)
            if match and "Amazon" in match.group(1):
                return True
        if "による発送" in full_text:
            match = re.search(r'(.*?)による発送', full_text)
            if match and "Amazon" in match.group(1):
                return True
        return False
    
    def _fetch_price_from_aod(self, asin: str) -> tuple[int, int]:
        """
        通常ページで価格が取れない場合、出品者一覧APIから価格と送料を取得する。
        戻り値: (price, shipping_fee)
        """
        url = (
            f"https://www.amazon.co.jp/gp/product/ajax/aodAjaxMain"
            f"/ref=dp_aod_unknown_mbc?asin={asin}&pc=dp"
        )
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 404:
                return 0, 0
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            offer = soup.select_one("#aod-offer")
            if offer:
                # 価格
                price = 0
                price_el = offer.select_one(".a-price-whole")
                if price_el:
                    price = self._extract_number(price_el.text)

                # 送料
                shipping_fee = 0
                shipping_el = offer.select_one(
                    "#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE span"
                )
                if shipping_el:
                    shipping_text = shipping_el.get_text()
                    shipping_fee = self._extract_shipping(shipping_text)

                return price, shipping_fee

        except Exception as e:
            print(f"AOD価格取得エラー: {e}")
        return 0, 0

    def _clean_text(self, text):
        return re.sub(r'\s+', ' ', text).strip() if text else ""

    def _extract_number(self, text):
        if not text:
            return 0
        num_str = re.sub(r'[^\d]', '', text)
        return int(num_str) if num_str else 0

    def _extract_shipping(self, text):
        if not text:
            return 0
        if "無料" in text:
            return 0
        match = re.search(r'[¥￥]\s*([\d,]+)', text)
        if match:
            return int(match.group(1).replace(',', ''))
        return 0

    def _parse_stock_status(self, stock_text):
        if not stock_text or "一時的に在庫切れ" in stock_text:
            return "out_of_stock"
        if "予約受付中" in stock_text:
            return "preorder"
        if "残り" in stock_text and "点" in stock_text:
            return "limited"
        if "在庫あり" in stock_text:
            return "in_stock"
        return "out_of_stock"