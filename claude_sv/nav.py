"""3点セット（ボード / マップ / 戦績）の共通ナビ。全ダッシュボードで統一。

遷移ルール（ふくちゃん決定 2026-08）:
  ライブボード＝母艦なので **同一タブ**／マップ・戦績・個別ページ＝**新タブ**（母艦を1枚保つ）。
各ページの header 右側にこの 3 リンクを差し、続けてテーマトグルボタンを置く。
CSS の .tgl / .tgl.cur / .nav は各ジェネレータ側に定義済み（同一スタイル）。
"""

# (key, ファイル名, ラベル, 新タブで開くか)
_ITEMS = [
    ("board",  "live_board.html",       "🖥️ ライブボード", False),
    ("map",    "claude_dashboard.html", "📊 アクティビティ", True),
    ("record", "track_record.html",     "🏆 トラックレコード", True),
]


def nav_html(current: str = "", rel: str = "") -> str:
    """統一ナビの HTML（<a class="tgl"> ×3）。

    current: 今いるページの key（"board"/"map"/"record"）。一致するものは非リンク強調（.cur）。
             個別ページなど 3 点セット外は "" を渡す（全部リンクになる）。
    rel:     相対プレフィックス（reports/track/ 配下の個別ページからは "../"）。
    """
    out = []
    for key, fname, label, blank in _ITEMS:
        if current and key == current:
            out.append(f'<span class="tgl cur">{label}</span>')
        else:
            tgt = ' target="_blank" rel="noopener"' if blank else ""
            out.append(f'<a class="tgl" href="{rel}{fname}"{tgt}>{label}</a>')
    return "".join(out)
