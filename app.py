from gui.gui_manager import App
import os
import sys

# App化されている場合、環境変数を明示的にセットする
if getattr(sys, 'frozen', False):
    # macOSのSSL証明書のパスを通す（スクレイピング失敗対策）
    os.environ['SSL_CERT_FILE'] = os.path.join(os.path.dirname(sys.executable), "certifi", "cacert.pem")
    # カレントディレクトリをアプリの横に強制変更
    os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))))))

if "--scheduler" in sys.argv:
    # GUIを起動せず、スケジューラのメイン処理だけを実行して終了する
    try:
        from scheduler import main 
        main()
    except Exception as e:
        print(f"Scheduler Error: {e}", file=sys.stderr)
    sys.exit(0)


app = App()
app.mainloop()