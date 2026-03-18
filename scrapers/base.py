from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """
    全スクレイパーの抽象基底クラス。
    新しいサイトを追加する際はこのクラスを継承し、
    search() と get_site_name() を実装する。
    """

    @abstractmethod
    def search(self, keyword: str) -> list[dict]:
        """
        キーワードで商品を検索し、以下のキーを持つ辞書のリストを返す。
        - name        : 商品名
        - price       : 価格（税込・整数）
        - shipping    : 送料（整数）
        - points      : 獲得ポイント（整数）
        - points_rate : ポイント還元率
        - unit_price  : 実質単価（float）
        - quantity    : 数量（整数）
        - product_url : 商品ページURL
        - image_url   : 画像URL
        - seller      : 販売店名
        - stock_status: 在庫状況（'in_stock'/'out_of_stock'/'limited'/'unknown'）
        - jan         : JANコード（取得できない場合はNone）
        """
        ...

    @abstractmethod
    def get_site_name(self) -> str:
        """
        サイト名を返す。DBのsite_nameと一致させること。
        例: 'Amazon', 'Rakuten', 'Yahoo'
        """
        ...