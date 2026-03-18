def normalize_to_gtin(code):
    """
    UPC(12桁)をJAN(13桁)に変換、またはJANをそのまま返す。
    バリデーションチェックも行う。
    """
    if not code or not code.isdigit():
        return None
    
    # 12桁の場合はUPCとみなし、頭に0を足して13桁のGTINにする
    if len(code) == 12:
        return '0' + code
    
    # 13桁の場合はそのまま（JAN/EAN）
    if len(code) == 13:
        return code
    
    # それ以外は不正な形式としてNoneを返す（またはログ出力）
    return None

# --- バリデーション使用例 ---
if __name__ == "__main__":
    raw_codes = ["4901234567890", "123456789012", "12345"]
    for c in raw_codes:
        gtin = normalize_to_gtin(c)
        print(f"Original: {c} -> Normalized: {gtin}")