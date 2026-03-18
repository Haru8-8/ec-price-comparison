import customtkinter as ctk
from tkinter import ttk
from tkinter import messagebox
import webbrowser
import requests
from io import BytesIO
from PIL import Image
from datetime import datetime
import threading
import queue
import matplotlib
import sys
import os
from pathlib import Path
matplotlib.use("Agg")  # tkinter組み込み用バックエンド（mainloopより前に必須、Tkに依存しないバックエンド）
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from services.search_engine import search_products
from services.price_comparison import get_price_comparison, get_price_history
from main import bridge_rakuten_to_amazon_yahoo
from db.db_manager import DatabaseManager
from config import Config

SITE_COLORS = {
    'Amazon': '#FF9900',
    'Rakuten': '#BF0000',
    'Yahoo': '#6A0DAD',
}

ALL_SITES = {'Amazon', 'Rakuten', 'Yahoo'}

SPINNER_FRAMES = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("EC価格比較ツール")
        self.geometry("1000x600")
        self._image_refs = []  # GC対策：CTkImageの参照を保持
        self._debounce_job = None
        self._scrape_queue = queue.Queue()
        self._db = DatabaseManager()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # タブビュー（ウィンドウ全体）
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.tabview.add("🔍 検索・比較")
        self.tabview.add("⚙ 設定")
 
        self._setup_search_tab()
        self._setup_settings_tab()
 
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._poll_scrape_queue()

    # =========================================================
    # 検索・比較タブ
    # =========================================================

    def _setup_search_tab(self):
        tab = self.tabview.tab("🔍 検索・比較")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        # 左ペイン
        self.left_frame = ctk.CTkFrame(tab)
        self.left_frame.grid(row=0, column=0, padx=(0, 5), sticky="nsew")
        self._setup_left_panel()

        # 右ペイン（スクロール対応）
        right_outer = ctk.CTkFrame(tab)
        right_outer.grid(row=0, column=1, padx=(5, 0), sticky="nsew")
        right_outer.grid_rowconfigure(0, weight=1)
        right_outer.grid_columnconfigure(0, weight=1)
        self.right_scroll = ctk.CTkScrollableFrame(right_outer)
        self.right_scroll.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(
            self.right_scroll, text="商品を選択すると比較結果が表示されます"
        ).pack(pady=50)

        # APIキー未設定時の警告エリア (初期は非表示)
        self.api_warning_frame = ctk.CTkFrame(self.left_frame, fg_color="#FFF3CD", height=0)
        self.api_warning_label = ctk.CTkLabel(
            self.api_warning_frame, 
            text="⚠️ Yahoo! Client IDが設定されていません。.envファイルを確認してください。",
            text_color="#856404",
            font=("Meiryo", 12, "bold")
        )
        self.api_warning_label.pack(pady=10, padx=10)
        
        # 起動時にチェックを実行
        self._check_api_key_status()

    def _setup_left_panel(self):
        self.search_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.search_frame.pack(fill="x", padx=10, pady=(10, 0))

        self.search_entry = ctk.CTkEntry(self.search_frame, placeholder_text="JAN or 名前")
        self.search_entry.pack(fill="x", pady=(0, 5))
        self.search_entry.bind("<Return>", lambda e: self._perform_search())

        self.search_button = ctk.CTkButton(self.search_frame, text="検索", command=self._perform_search)
        self.search_button.pack(fill="x", pady=(0, 5))

        # データ取得ボタン
        self.scrape_button = ctk.CTkButton(
            self.search_frame, text="🔄 データ取得",
            fg_color="#2E7D32", hover_color="#1B5E20",
            command=self._start_scraping
        )
        self.scrape_button.pack(fill="x", pady=(0, 5))

        # 件数表示
        self.count_label = ctk.CTkLabel(
            self.left_frame, text="", font=("Meiryo", 11), text_color="gray"
        )
        self.count_label.pack(anchor="w", padx=12)

        # Treeview（SITESカラム追加）
        self.tree = ttk.Treeview(
            self.left_frame,
            columns=("ID", "JAN", "NAME", "SITES"),
            show="headings"
        )
        self.tree.heading("ID", text="ID")
        self.tree.heading("JAN", text="JANコード")
        self.tree.heading("NAME", text="商品名")
        self.tree.heading("SITES", text="サイト")
        self.tree.column("ID", width=0, stretch=False)
        self.tree.column("JAN", width=100)
        self.tree.column("NAME", width=190)
        self.tree.column("SITES", width=60, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        # 3サイト未揃いの商品をグレーで表示
        self.tree.tag_configure("incomplete", foreground="gray")

        self.tree.bind("<<TreeviewSelect>>", self._on_item_selected)

    def _check_api_key_status(self):
        """APIキーの状態を確認し、UIを更新する"""
        if not Config.validate_api_key():
            # 警告を表示
            self.api_warning_frame.pack(fill="x", padx=10, pady=(0, 10), before=self.search_frame)
            # 検索ボタンを無効化（または警告付きで動作させる）
            self.scrape_button.configure(state="disabled", text="ID未設定のため実行不可")
        else:
            self.api_warning_frame.pack_forget()
            self.scrape_button.configure(state="normal", text="検索 & 取得")

    # =========================================================
    # 設定タブ
    # =========================================================
 
    def _setup_settings_tab(self):
        tab = self.tabview.tab("⚙ 設定")
        tab.grid_columnconfigure(0, weight=1)
 
        scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        tab.grid_rowconfigure(0, weight=1)

        sched_config = Config.get_scheduler_config()

        # ---- 基本設定 ----
        ctk.CTkLabel(
            scroll, text="基本設定", font=("Meiryo", 15, "bold")
        ).pack(anchor="w", pady=(0, 10))

        # 取得ページ数設定
        page_row = ctk.CTkFrame(scroll, fg_color="transparent")
        page_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(page_row, text="検索取得ページ数", font=("Meiryo", 13)).pack(side="left")
        
        # コンボボックス
        self._page_var = ctk.StringVar(value=f"{sched_config.get('max_pages', 1)} ページ")
        self._page_combo = ctk.CTkComboBox(
            page_row, 
            values=["1 ページ", "2 ページ", "3 ページ"],
            variable=self._page_var,
            width=100,
            command=self._update_estimated_time # ★選択時に時間を計算
        )
        self._page_combo.pack(side="right", padx=(10, 0))
        
        # 予想時間ラベル
        self._est_time_label = ctk.CTkLabel(
            page_row, text="", font=("Meiryo", 11), text_color="gray"
        )
        self._est_time_label.pack(side="right")
        self._update_estimated_time(self._page_var.get(), save=False) # 初期表示用

        sep_base = ctk.CTkFrame(scroll, height=1, fg_color="gray")
        sep_base.pack(fill="x", pady=(10, 12))
 
        # ---- 自動巡回設定 ----
        ctk.CTkLabel(
            scroll, text="自動巡回設定", font=("Meiryo", 15, "bold")
        ).pack(anchor="w", pady=(0, 10))
 
        # on/off トグル
        toggle_row = ctk.CTkFrame(scroll, fg_color="transparent")
        toggle_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(toggle_row, text="自動巡回", font=("Meiryo", 13)).pack(side="left")
        self._sched_enabled = ctk.BooleanVar(value=sched_config.get('enabled', False))
        ctk.CTkSwitch(
            toggle_row, text="", variable=self._sched_enabled,
            command=self._save_scheduler_config, width=44
        ).pack(side="right")
 
        # 実行時刻
        time_row = ctk.CTkFrame(scroll, fg_color="transparent")
        time_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(time_row, text="実行時刻 (HH:MM)", font=("Meiryo", 13)).pack(side="left")
        self._run_time_entry = ctk.CTkEntry(time_row, width=80, placeholder_text="08:00")
        self._run_time_entry.insert(0, sched_config.get('run_time', '08:00'))
        self._run_time_entry.pack(side="right")
        self._run_time_entry.bind("<FocusOut>", lambda e: self._save_scheduler_config())
 
        # launchd登録
        launchd_row = ctk.CTkFrame(scroll, fg_color="transparent")
        launchd_row.pack(fill="x", pady=(0, 4))
        self._launchd_status_label = ctk.CTkLabel(
            launchd_row, text="", font=("Meiryo", 12)
        )
        self._launchd_status_label.pack(side="left")
        self._launchd_btn = ctk.CTkButton(
            launchd_row, text="", width=90, height=28,
            font=("Meiryo", 11), command=self._toggle_launchd
        )
        self._launchd_btn.pack(side="right")
        self._update_launchd_status()
 
        ctk.CTkLabel(
            scroll,
            text="※ launchd登録後はGUIを閉じていても毎日指定時刻に自動取得されます",
            font=("Meiryo", 10), text_color="gray"
        ).pack(anchor="w", pady=(0, 20))
 
        # ---- 検索履歴設定 ----
        sep = ctk.CTkFrame(scroll, height=1, fg_color="gray")
        sep.pack(fill="x", pady=(0, 12))
 
        ctk.CTkLabel(
            scroll, text="検索履歴ベースの自動追加", font=("Meiryo", 15, "bold")
        ).pack(anchor="w", pady=(0, 8))
 
        # 集計期間
        days_row = ctk.CTkFrame(scroll, fg_color="transparent")
        days_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(days_row, text="集計期間 (日)", font=("Meiryo", 13)).pack(side="left")
        self._history_days_entry = ctk.CTkEntry(days_row, width=60)
        self._history_days_entry.insert(0, str(sched_config.get('history_days', 7)))
        self._history_days_entry.pack(side="right")
        self._history_days_entry.bind("<FocusOut>", lambda e: self._save_scheduler_config())
 
        # 最低検索回数
        count_row = ctk.CTkFrame(scroll, fg_color="transparent")
        count_row.pack(fill="x", pady=(0, 20))
        ctk.CTkLabel(count_row, text="最低検索回数", font=("Meiryo", 13)).pack(side="left")
        self._history_min_entry = ctk.CTkEntry(count_row, width=60)
        self._history_min_entry.insert(0, str(sched_config.get('history_min_count', 3)))
        self._history_min_entry.pack(side="right")
        self._history_min_entry.bind("<FocusOut>", lambda e: self._save_scheduler_config())
 
        # ---- 登録済みキーワード ----
        sep2 = ctk.CTkFrame(scroll, height=1, fg_color="gray")
        sep2.pack(fill="x", pady=(0, 12))
 
        ctk.CTkLabel(
            scroll, text="巡回キーワード管理", font=("Meiryo", 15, "bold")
        ).pack(anchor="w", pady=(0, 8))
 
        # キーワード追加入力欄
        add_row = ctk.CTkFrame(scroll, fg_color="transparent")
        add_row.pack(fill="x", pady=(0, 8))
        self._kw_add_entry = ctk.CTkEntry(add_row, placeholder_text="キーワードを入力")
        self._kw_add_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._kw_add_entry.bind("<Return>", lambda e: self._add_scheduled_keyword())
        ctk.CTkButton(
            add_row, text="追加", width=60, height=30,
            command=self._add_scheduled_keyword
        ).pack(side="right")
 
        # 登録済みキーワード一覧
        self._kw_listbox = ctk.CTkScrollableFrame(scroll, height=150)
        self._kw_listbox.pack(fill="x")
        self._refresh_keyword_list()

    # =========================================================
    # 検索・比較ロジック
    # =========================================================

    def _display_results(self, items):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for item in items:
            sites = item['sites']
            site_icons = (
                ('🟠' if 'Amazon' in sites else '⚪') +
                ('🔴' if 'Rakuten' in sites else '⚪') +
                ('🟣' if 'Yahoo' in sites else '⚪')
            )
            tag = "" if sites == ALL_SITES else "incomplete"
            
            self.tree.insert(
                "", "end",
                values=(item['id'], item['gtin'], item['product_name'], site_icons),
                tags=(tag,)
            )
 
        count = len(items)
        self.count_label.configure(
            text=f"{count}件見つかりました" if count else "該当なし"
        )

    def _perform_search(self):
        keyword = self.search_entry.get().strip()
        if not keyword:
            return
        self._db.record_search_history(keyword)
        items = search_products(keyword)
        self._display_results(items)

    def _start_scraping(self):
        """スクレイピングをバックグラウンドで実行する"""
        if not Config.validate_api_key():
            messagebox.showwarning(
                "設定不足", 
                "Yahoo! Client IDが設定されていません。\n"
                "1. アプリと同じ階層に .env ファイルを配置\n"
                "2. YAHOO_CLIENT_ID=あなたのID を記述\n"
                "3. アプリを再起動してください。"
            )
            return
    
        keyword = self.search_entry.get().strip()
        if not keyword:
            return
 
        self.scrape_button.configure(state="disabled", text="取得中...")
        # スクレイピング中は商品選択・検索を無効化
        self.tree.unbind("<<TreeviewSelect>>")
        self.search_button.configure(state="disabled")
        self.search_entry.configure(state="disabled")

        # プログレスバーと進捗ラベルを追加
        self._progress_bar = ctk.CTkProgressBar(self.left_frame)
        self._progress_bar.pack(fill="x", padx=10, pady=(0, 2))
        self._progress_bar.set(0)
        self._progress_label = ctk.CTkLabel(
            self.left_frame, text="準備中...", font=("Meiryo", 10), text_color="gray"
        )
        self._progress_label.pack(anchor="w", padx=12, pady=(0, 4))

        # Treeviewにローディングオーバーレイを表示
        self._show_tree_loading()

        # 設定からmax_pagesを取得
        sched_config = Config.get_scheduler_config()
        max_pages = sched_config.get('max_pages', 1)

        def on_progress(current, total, name):
            self._scrape_queue.put(("progress", current / max(total, 1), name))
 
        def run():
            try:
                bridge_rakuten_to_amazon_yahoo(
                    keyword,
                    progress_callback=on_progress,
                    max_pages=max_pages
                )
                self._scrape_queue.put(("done", keyword))
            except Exception as e:
                print(f"スクレイピングエラー: {e}")
                self._scrape_queue.put(("error", keyword))

        threading.Thread(target=run, daemon=True).start()

    def _poll_scrape_queue(self):
        """メインスレッドがスクレイピング完了フラグをポーリングする"""
        try:
            while True:
                item = self._scrape_queue.get_nowait()
                if item[0] == "progress":
                    _, ratio, name = item
                    if hasattr(self, '_progress_bar'):
                        self._progress_bar.set(ratio)
                        self._progress_label.configure(text=f"{name}...")
                elif item[0] in ("done", "error"):
                    self._on_scraping_done(item[1])
        except queue.Empty:
            pass
        finally:
            if self.winfo_exists():
                self.after(200, self._poll_scrape_queue)

    def _on_scraping_done(self, keyword):
        """スクレイピング完了後の処理"""
        # プログレスバーとオーバーレイを削除
        if hasattr(self, '_progress_bar'):
            self._progress_bar.destroy()
            self._progress_label.destroy()
            del self._progress_bar
            del self._progress_label
        self._hide_tree_loading()

        self.scrape_button.configure(state="normal", text="🔄 データ取得")
        # 操作を再度有効化
        self.tree.bind("<<TreeviewSelect>>", self._on_item_selected)
        self.search_button.configure(state="normal")
        self.search_entry.configure(state="normal")
        items = search_products(keyword)
        self._display_results(items)

    def _show_tree_loading(self):
        """Treeviewの上にローディングオーバーレイを表示"""
        self._overlay = ctk.CTkFrame(
            self.left_frame, fg_color=("#AAAAAA", "#444444")
        )
        self._overlay.place(in_=self.tree, x=0, y=0, relwidth=1, relheight=1)
 
        self._spinner_label = ctk.CTkLabel(
            self._overlay, text="⠋", font=("Meiryo", 32)
        )
        self._spinner_label.place(relx=0.5, rely=0.45, anchor="center")
 
        self._spinner_text = ctk.CTkLabel(
            self._overlay, text="取得中...", font=("Meiryo", 11)
        )
        self._spinner_text.place(relx=0.5, rely=0.6, anchor="center")
 
        self._animate_spinner(0)

    def _animate_spinner(self, idx):
        """スピナーアニメーションを繰り返す"""
        if hasattr(self, '_spinner_label'):
            self._spinner_label.configure(text=SPINNER_FRAMES[idx % len(SPINNER_FRAMES)])
            self.after(80, self._animate_spinner, idx + 1)

    def _hide_tree_loading(self):
        """ローディングオーバーレイを削除"""
        if hasattr(self, '_overlay'):
            self._overlay.destroy()
            del self._overlay
        if hasattr(self, '_spinner_label'):
            del self._spinner_label
        if hasattr(self, '_spinner_text'):
            del self._spinner_text

    def _on_item_selected(self, event):
        selected_item = self.tree.focus()
        item_data = self.tree.item(selected_item, 'values')
        if not item_data:
            return

        # デバウンス：150ms以内の連続選択は最後の1回だけ実行
        if self._debounce_job:
            self.after_cancel(self._debounce_job)

        product_id = item_data[0]
        self._debounce_job = self.after(
            250,
            lambda: self._display_comparison_details(product_id)
        )

    def _display_comparison_details(self, product_id):
        self._debounce_job = None
        comparison = get_price_comparison(product_id)
        if not comparison:
            return

        # 右ペインをクリア
        for widget in self.right_scroll.winfo_children():
            widget.destroy()
        self._image_refs = []

        ctk.CTkLabel(self.right_scroll, text="価格比較詳細", font=("Meiryo", 20, "bold")).pack(pady=15)

        # 取得済みデータのマッピング（サイト名をキーにする）
        available_data = {p['site']: p for p in comparison['prices']}
        
        # 常に全サイト分のカードを作成
        for site_name in sorted(list(ALL_SITES)):
            data = available_data.get(site_name)
            if data:
                is_best = (data['unit_price'] == comparison['best_unit_price'])
                self._build_card(data, is_best)
            else:
                # データがない場合はグレーアウトしたカードを表示
                self._build_empty_card(site_name)

        # 最終更新日時
        latest_ts = max(
            (p.get('timestamp') for p in comparison['prices'] if p.get('timestamp')),
            default=None
        )
        if latest_ts:
            try:
                ts_str = datetime.fromisoformat(latest_ts).strftime('%Y/%m/%d %H:%M')
            except Exception:
                ts_str = latest_ts
            ctk.CTkLabel(
                self.right_scroll,
                text=f"最終更新: {ts_str}",
                font=("Meiryo", 10), text_color="gray"
            ).pack(anchor="e", padx=15, pady=(0, 5))

        # 期間切り替えボタン＋グラフエリア
        chart_frame = ctk.CTkFrame(self.right_scroll, fg_color="transparent")
        chart_frame.pack(fill="x", padx=15, pady=(5, 0))

        ctk.CTkLabel(chart_frame, text="価格推移", font=("Meiryo", 14, "bold")).pack(anchor="w")

        # 期間切り替えボタン
        btn_frame = ctk.CTkFrame(chart_frame, fg_color="transparent")
        btn_frame.pack(anchor="w", pady=5)
        for label, days in [("7日", 7), ("30日", 30), ("90日", 90), ("全期間", 3650)]:
            ctk.CTkButton(
                btn_frame, text=label, width=55, height=26,
                command=lambda d=days, f=chart_frame, pid=product_id: self._reload_chart(f, pid, d)
            ).pack(side="left", padx=3)

        # デフォルト30日で描画
        self._embed_chart(chart_frame, product_id)

        # 描画完了後にフォーカスを確実に戻す
        self.after(50, self.tree.focus_set)  # ← 追加（50ms後に戻す）

    def _build_card(self, p, is_best):
        card = ctk.CTkFrame(
            self.right_scroll,
            corner_radius=15,
            fg_color="#F5F5F5",
            border_width=2 if is_best else 0,
            border_color="gold" if is_best else None
        )
        card.pack(fill="x", padx=15, pady=10)
 
        content_frame = ctk.CTkFrame(card, fg_color="transparent")
        content_frame.pack(fill="x", padx=10, pady=10)
 
        image_url = p.get('image_url')
        img = self._get_image_from_url(image_url) if image_url else None
        img_label = ctk.CTkLabel(
            content_frame, text="" if img else "No Image",
            image=img, width=100, height=100
        )
        img_label.pack(side="left", padx=(5, 15))
        if img:
            self._image_refs.append(img)
 
        product_url = p.get('product_url')
        img_label.bind("<Button-1>", lambda e, url=product_url: self._open_url(url))
        img_label.configure(cursor="hand2")
 
        info_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True)
 
        header = ctk.CTkFrame(info_frame, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text=f"{p['site']} {'🔥DEAL' if p['is_deal'] else ''}",
            font=("Meiryo", 16, "bold"), text_color="#202020"
        ).pack(side="left")
        if is_best:
            ctk.CTkLabel(
                header, text=" 🏆 最安値 ",
                fg_color="#D4AF37", text_color="white",
                corner_radius=4, font=("Meiryo", 10, "bold")
            ).pack(side="right")
 
        ctk.CTkLabel(
            info_frame, text=f"¥{p['price']:,}",
            font=("Verdana", 28, "bold"), text_color="#1A1A1A"
        ).pack(anchor="w")

        # 数量が2以上の場合のみ表示
        if p.get('quantity', 1) > 1:
            ctk.CTkLabel(
                info_frame,
                text=f"※ {p['quantity']}点セット価格",
                font=("Meiryo", 10), text_color="gray"
            ).pack(anchor="w")
 
        sub_text = f"{p['points']}pt 獲得  |  実質単価: ¥{p['unit_price']:.1f}/個"
        ctk.CTkLabel(
            info_frame, text=sub_text,
            font=("Meiryo", 12, "bold"), text_color="#008000"
        ).pack(anchor="w")
 
        shop_link = ctk.CTkLabel(
            info_frame, text=f"ショップ: {p['seller']}",
            font=("Meiryo", 11), text_color="blue", cursor="hand2"
        )
        shop_link.pack(anchor="w", pady=(5, 0))
        shop_link.bind("<Button-1>", lambda e, url=product_url: self._open_url(url))
 
        if p['review_avg']:
            review_text = f"★ {p['review_avg']:.2f} ({p['review_count']:,}件)"
        else:
            review_text = "★ --- (件)"
        ctk.CTkLabel(
            card, text=review_text,
            font=("Meiryo", 11), text_color="gray"
        ).pack(anchor="e", padx=15, pady=(0, 10))

    def _build_empty_card(self, site_name):
        card = ctk.CTkFrame(
            self.right_scroll,
            corner_radius=15,
            fg_color="#E0E0E0", # グレー背景
            border_width=0
        )
        card.pack(fill="x", padx=15, pady=10)
 
        content_frame = ctk.CTkFrame(card, fg_color="transparent")
        content_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(
            content_frame, text=f"{site_name}\n取得失敗または取扱なし",
            font=("Meiryo", 14, "bold"), text_color="#757575",
            width=200, height=130
        ).pack(padx=20)

    def _get_image_from_url(self, url, size=(100, 100)):
        try:
            response = requests.get(url, timeout=5)
            img_data = BytesIO(response.content)
            img = Image.open(img_data)
            return ctk.CTkImage(light_image=img, dark_image=img, size=size)
        except Exception as e:
            print(f"画像読み込みエラー: {e}")
            return None

    def _open_url(self, url):
        if url:
            webbrowser.open(url)

    def _build_price_chart(self, product_id, days=30):
        """価格推移グラフをFigureとして返す"""
        history = get_price_history(product_id, days=days)
        if not history:
            return None

        fig, ax = plt.subplots(figsize=(5, 2.5), dpi=80)
        fig.patch.set_facecolor('#F0F0F0')
        ax.set_facecolor('#F8F8F8')

        all_dates = []
        for site, records in history.items():
            if not records:
                continue
            dates = [r['timestamp'] for r in records]
            prices = [r['unit_price'] for r in records]
            all_dates.extend(dates)
            color = SITE_COLORS.get(site, '#888888')
            ax.plot(dates, prices, label=site, color=color, linewidth=2, marker='o', markersize=4)

        unique_dates = sorted(set(all_dates))
        n = len(unique_dates)
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, n // 6)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        fig.autofmt_xdate(rotation=45)
        ax.set_ylabel('Unit Price (¥)', fontsize=9)
        ax.legend(fontsize=8, loc='upper right')
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=8)
        plt.tight_layout()

        return fig

    def _embed_chart(self, parent, product_id, days=30):
        """グラフをtkinterフレームに埋め込む"""
        fig = self._build_price_chart(product_id, days=days)
        if fig is None:
            ctk.CTkLabel(parent, text="履歴データがありません", text_color="gray").pack(pady=10)
            return
        
        # FigをPNG画像としてメモリに書き出してCTkImageで表示
        # FigureCanvasTkAggを使わないのでTkとmatplotlibの競合が発生しない
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        plt.close(fig)

        pil_img = Image.open(buf)
        chart_img = ctk.CTkImage(
            light_image=pil_img, dark_image=pil_img,
            size=(500, 220)
        )
        self._image_refs.append(chart_img)

        chart_label = ctk.CTkLabel(parent, text="", image=chart_img)
        chart_label.pack(fill="x", padx=15, pady=(0, 15))

    def _reload_chart(self, chart_frame, product_id, days):
        """期間ボタン押下時にグラフを再描画"""
        # 既存のcanvasだけ削除（ボタンは残す）
        for widget in chart_frame.winfo_children():
            if isinstance(widget.winfo_class(), str) and 'canvas' in widget.winfo_class().lower():
                widget.destroy()
        # FigureCanvasTkAggのtkウィジェットはFrameとして認識されるので
        # pack順で最後の要素（グラフ）だけ削除する
        children = chart_frame.winfo_children()
        if len(children) > 2:  # ラベル＋ボタン行＋グラフ
            children[-1].destroy()
        self._embed_chart(chart_frame, product_id, days)

    # =========================================================
    # 設定タブロジック
    # =========================================================
 
    def _update_estimated_time(self, choice, save=True):
        """選択されたページ数から予想時間を計算してラベルを更新"""
        try:
            pages = int(choice.split()[0])
            
            items_per_page = 45  # 楽天の1ページあたりの最大件数
            sec_per_item = 6.0   # 楽天詳細1s + Amazon4s + Yahoo1s = 5s

            total_seconds = pages * items_per_page * sec_per_item
            est_minutes = total_seconds / 60

            self._est_time_label.configure(text=f"(予想: 約 {int(est_minutes)} 分 / 1キーワード)")
            if save and hasattr(self, '_run_time_entry'):
                self._save_scheduler_config() # 変更を保存
        except (ValueError, AttributeError):
            pass

    def _save_scheduler_config(self):
        """設定をconfig.jsonに保存しplistも更新する"""
        try:
            run_time = self._run_time_entry.get().strip() or '08:00'
            history_days = int(self._history_days_entry.get().strip() or '7')
            history_min = int(self._history_min_entry.get().strip() or '3')
            max_pages = int(self._page_var.get().split()[0] or '1')
        except ValueError:
            return
        config = Config.get_scheduler_config()
        config['enabled'] = self._sched_enabled.get()
        config['run_time'] = run_time
        config['history_days'] = history_days
        config['history_min_count'] = history_min
        config['max_pages'] = max_pages
        Config.save_scheduler_config(config)
 
    def _update_launchd_status(self):
        if Config.is_launchd_registered():
            self._launchd_status_label.configure(text="launchd: 登録済み", text_color="green")
            self._launchd_btn.configure(text="登録解除")
        else:
            self._launchd_status_label.configure(text="launchd: 未登録", text_color="gray")
            self._launchd_btn.configure(text="登録する")
 
    def _toggle_launchd(self):
        if Config.is_launchd_registered():
            Config.remove_launchd()
        else:
            # 1. 実行コマンド（リスト）の作成
            if getattr(sys, 'frozen', False):
                # App化されている場合: [アプリバイナリ, --scheduler]
                exec_args = [sys.executable, "--scheduler"]
                
                # WorkingDirectory: MacOS/アプリ名 から4つ上がって .app の外側
                exe_path = os.path.abspath(sys.executable)
                working_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(exe_path))))
            else:
                # 開発環境の場合: [pythonのパス, app.pyのパス, --scheduler]
                current_script = os.path.abspath(sys.argv[0]) # 通常は project/app.py
                exec_args = [sys.executable, current_script, "--scheduler"]
                
                # WorkingDirectory: app.py の親ディレクトリ
                working_dir = os.path.dirname(current_script)

            # 2. Config経由でplistを生成
            Config.setup_launchd(exec_args, working_dir)
        self._update_launchd_status()
 
    def _add_scheduled_keyword(self):
        keyword = self._kw_add_entry.get().strip()
        if not keyword:
            return
        self._db.add_scheduled_keyword(keyword)
        self._kw_add_entry.delete(0, "end")
        self._refresh_keyword_list()
 
    def _remove_scheduled_keyword(self, keyword: str):
        self._db.remove_scheduled_keyword(keyword)
        self._refresh_keyword_list()
 
    def _refresh_keyword_list(self):
        for widget in self._kw_listbox.winfo_children():
            widget.destroy()
        keywords = self._db.get_scheduled_keywords()
        if not keywords:
            ctk.CTkLabel(
                self._kw_listbox, text="未登録",
                font=("Meiryo", 10), text_color="gray"
            ).pack(anchor="w")
            return
        for item in keywords:
            row = ctk.CTkFrame(self._kw_listbox, fg_color="transparent")
            row.pack(fill="x", pady=2)
            last = item.get('last_run_at')
            next_ = item.get('next_run_at')
            sub = ""
            if last:
                try:
                    sub = f"前回: {datetime.fromisoformat(last).strftime('%m/%d %H:%M')}"
                except Exception:
                    pass
            ctk.CTkLabel(
                row, text=item['keyword'], font=("Meiryo", 12), anchor="w"
            ).pack(side="left")
            if sub:
                ctk.CTkLabel(
                    row, text=sub, font=("Meiryo", 9), text_color="gray"
                ).pack(side="left", padx=(6, 0))
            ctk.CTkButton(
                row, text="✕", width=26, height=22,
                fg_color="transparent", hover_color="#FFCCCC", text_color="gray",
                command=lambda kw=item['keyword']: self._remove_scheduled_keyword(kw)
            ).pack(side="right")


if __name__ == "__main__":
    app = App()
    app.mainloop()