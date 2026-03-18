#!/bin/bash

# 1. 以前のビルド成果物を削除してクリーンにする
rm -rf build dist
rm EC価格比較ツール.spec

# 2. PyInstallerを実行
# ※ここではアプリ内部に .env.example を含める必要はないため --add-data から外してもOKです
pyinstaller --noconsole --onedir --windowed \
  --name "EC価格比較ツール" \
  --add-data "gui:gui" \
  --add-data "db:db" \
  --add-data "scrapers:scrapers" \
  --add-data "services:services" \
  --collect-all customtkinter \
  app.py

# 3. 【重要】distフォルダ内のアプリの「横」に .env.example をコピーする
cp .env.example dist/.env.example

# 4. ついでに README も横にあると親切です
if [ -f "README.md" ]; then
  cp README.md dist/README.md
fi

echo "------------------------------------------"
echo "ビルド完了！"
echo "dist フォルダを確認してください。"
echo "------------------------------------------"