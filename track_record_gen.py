"""トラック戦績ボード: 各案件の「今」と「あゆみ」を1画面に。

3点セットの「戦績=あゆみ」担当。
  ボード=いま（live_board.html）  マップ=ぜんたい（claude_dashboard.html）
  戦績=あゆみ（track_record.html）← このスクリプト

データ源:
  1. メモリ台帳（~/.claude/projects/*/memory/ の type:project）
  2. project_dirs（config の project_dirs）        ← OneDriveのprojects/等
  3. extra_track_files（config の extra_track_files）← gitリポジトリの現在地ファイル等

  python track_record_gen.py            reports/track_record.html を生成して開く
  python track_record_gen.py --no-open  生成だけ

出力: reports/track_record.html
"""
import html as html_mod
import io
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from claude_sv.ledger import (
    load_extra_files, load_ledgers, load_project_dirs, load_track_file,
)
from claude_sv.mapping import ProjectMapper, load_config

REPO_ROOT = Path(__file__).parent
OUT_DIR = REPO_ROOT / "reports"
TODAY = datetime.now().date()

# ── 状態バッジ ─────────────────────────────────────────────────
def _activity_status(latest_date: str) -> tuple:
    if not latest_date:
        return "—", "status-none"
    try:
        from datetime import datetime as dt
        d = dt.strptime(latest_date, "%Y-%m-%d").date()
        delta = (TODAY - d).days
    except ValueError:
        return "—", "status-none"
    if delta <= 7:
        return f"🟢 {delta}日前", "status-hot"
    elif delta <= 30:
        return f"🟡 {delta}日前", "status-warm"
    else:
        return f"⚫ {delta}日前", "status-cold"


# ── 簡易 markdown → HTML（短文スニペット向け）─────────────────────
def _simple_html(text: str) -> str:
    s = html_mod.escape((text or "").strip())
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # 箇条書き記号を除去
    s = re.sub(r"^(?:&bull;|[-*・])\s+", "", s, flags=re.M)
    return s


# ── CSS ────────────────────────────────────────────────────────
HEAD_JS = ("try{var t=localStorage.getItem('csv-theme');"
           "if(t)document.documentElement.dataset.theme=t;}catch(e){}")
TGL_JS = """(function(){
  var root=document.documentElement, btn=document.getElementById('tgl');
  function lab(){btn.textContent=root.dataset.theme==='light'?'☾ ダーク':'☀ ライト';}
  lab();
  btn.onclick=function(){
    root.dataset.theme=root.dataset.theme==='light'?'dark':'light';
    try{localStorage.setItem('csv-theme',root.dataset.theme);}catch(e){}
    lab();
  };
})();"""

CSS = """
*{box-sizing:border-box}
:root{
  color-scheme:dark;
  --plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--border:rgba(255,255,255,.10);--wash:rgba(255,255,255,.055);
  --hot:#22c55e;--warm:#eab308;--cold:#6b7280;
  --blocked:#ef4444;--now:#3b82f6;--cat-1:#3987e5;
}
:root[data-theme="light"]{
  color-scheme:light;
  --plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--border:rgba(11,11,11,.10);--wash:rgba(11,11,11,.045);
  --hot:#16a34a;--warm:#ca8a04;--cold:#6b7280;
  --blocked:#dc2626;--now:#2563eb;--cat-1:#2a78d6;
}
body{margin:0;padding:22px 20px 60px;background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;
  font-size:13px;line-height:1.62;-webkit-font-smoothing:antialiased}
.wrap{max-width:1280px;margin:0 auto}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:20px}
h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.01em}
.sub{color:var(--muted);font-size:11.5px;margin-top:3px}
.nav{display:flex;gap:8px;align-items:center}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;
  padding:5px 13px;font-size:11.5px;cursor:pointer;font-family:inherit;white-space:nowrap;
  text-decoration:none;display:inline-block}
.tgl:hover{background:var(--wash);color:var(--ink)}
h2{font-size:12.5px;font-weight:600;color:var(--ink2);margin:28px 0 12px;
  letter-spacing:.04em;display:flex;align-items:center;gap:9px}
h2 .n{color:var(--muted);font-weight:400;font-size:11px}
/* グリッド */
.track-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px}
/* カード */
.track-card{background:var(--surface);border:1px solid var(--border);border-radius:11px;
  padding:14px 16px;display:flex;flex-direction:column;gap:10px}
/* ヘッダー行 */
.track-header{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px}
.track-name{font-size:14px;font-weight:600;color:var(--ink);flex-shrink:0}
.status-badge{font-size:10.5px;border-radius:999px;padding:1px 8px;white-space:nowrap}
.status-hot{color:var(--hot);border:1px solid var(--hot)}
.status-warm{color:var(--warm);border:1px solid var(--warm)}
.status-cold{color:var(--cold);border:1px solid var(--cold)}
.status-none{color:var(--muted);border:1px solid var(--muted)}
.track-stats{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}
/* 現在地 / Blocked */
.track-section{border-top:1px solid var(--border);padding-top:8px}
.section-label{font-size:10.5px;color:var(--muted);margin-bottom:4px}
.section-now .section-label{color:var(--now)}
.section-blocked .section-label{color:var(--blocked)}
.section-text{color:var(--ink2);font-size:12px}
.section-text strong{color:var(--ink)}
.section-text code{background:var(--wash);border-radius:4px;padding:1px 5px;
  font-size:11px;font-family:ui-monospace,Consolas,monospace}
/* セッション履歴 */
.track-history{border-top:1px solid var(--border);padding-top:8px}
.history-label{font-size:10.5px;color:var(--muted);margin-bottom:5px}
.s-row{display:flex;align-items:baseline;gap:6px;font-size:12px;
  padding:2px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.s-row:last-child{border-bottom:none}
.s-icon{font-size:11px;width:18px;flex-shrink:0}
.s-num{font-weight:600;color:var(--ink);width:28px;flex-shrink:0;font-size:11.5px}
.s-date{color:var(--muted);font-size:11px;width:38px;flex-shrink:0;font-family:ui-monospace,Consolas,monospace}
.s-sum{color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;font-size:11.5px}
.s-result{color:var(--muted);font-size:11px;flex-shrink:0;max-width:120px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s-row.done .s-num{color:var(--hot)}
.s-row.pending .s-num{color:var(--muted)}
/* 戦績なし */
.no-hist{display:flex;flex-wrap:wrap;gap:8px;padding:8px 0}
.no-hist-item{color:var(--muted);font-size:11.5px;background:var(--wash);
  border-radius:6px;padding:3px 10px}
/* フッター */
footer{color:var(--muted);font-size:11px;margin-top:34px;border-top:1px solid var(--border);
  padding-top:11px}
.ws-label{color:var(--muted);font-size:10.5px;font-family:ui-monospace,Consolas,monospace;
  background:var(--wash);border-radius:4px;padding:0 5px;margin-left:4px}
"""


# ── ページ組み立て ─────────────────────────────────────────────
def _page(title: str, body: str) -> str:
    nav = ('<a class="tgl" href="live_board.html">🖥️ ライブボード</a>'
           '<a class="tgl" href="claude_dashboard.html">🗺️ マップ</a>'
           '<a class="tgl" href="counter.html">🎫 カウンター</a>')
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html_mod.escape(title)}</title><script>{HEAD_JS}</script>'
            f'<style>{CSS}</style></head><body><div class="wrap">'
            f'<header><div><h1>{html_mod.escape(title)}</h1>'
            f'<div class="sub">データ源は台帳ファイル本体（正本）。'
            f'この画面は毎回そこから描き直しているだけ（内容のコピーは持たない）'
            f'　生成 {datetime.now():%m/%d %H:%M}</div></div>'
            f'<div class="nav">{nav}<button class="tgl" id="tgl"></button></div>'
            f'</header>{body}</div><script>{TGL_JS}</script></body></html>')


# ── カード HTML ───────────────────────────────────────────────
def _session_row_html(entry) -> str:
    date_short = entry.date[5:] if len(entry.date) >= 10 else entry.date  # "08-04"
    icon = "✅" if entry.is_done else "⏳"
    row_cls = "done" if entry.is_done else "pending"
    summary = entry.summary[:55] + ("…" if len(entry.summary) > 55 else "")
    result_text = re.sub(r"✅\s*", "", entry.result)[:35] if entry.result else "—"
    return (f'<div class="s-row {row_cls}">'
            f'<span class="s-icon">{icon}</span>'
            f'<span class="s-num">{html_mod.escape(entry.session)}</span>'
            f'<span class="s-date">{date_short}</span>'
            f'<span class="s-sum" title="{html_mod.escape(entry.result)}">'
            f'{html_mod.escape(summary)}</span>'
            f'<span class="s-result">{html_mod.escape(result_text)}</span>'
            f'</div>')


def _track_card_html(led) -> str:
    hist = [e for e in led.session_history() if not e.is_reserved]
    last3 = list(reversed(hist[-3:]))  # 新しい順 (最大3件)
    count = len(hist)
    since = led.active_since()
    latest = led.latest_session_date()

    status_label, status_cls = _activity_status(latest)

    # 統計テキスト
    stats_parts = [f"累計 {count}S"]
    if since:
        stats_parts.append(since[5:])  # "07-29"
        if latest and latest != since:
            stats_parts.append(f"〜 {latest[5:]}")
    stats = " | ".join(stats_parts)

    # ソース表示（ws_label）
    ws_span = f'<span class="ws-label">{html_mod.escape(led.ws_label)}</span>' if led.ws_label else ""

    # ヘッダー
    header = (f'<div class="track-header">'
              f'<span class="track-name">{html_mod.escape(led.label)}</span>{ws_span}'
              f'<span class="status-badge {status_cls}">{status_label}</span>'
              f'<span class="track-stats">{html_mod.escape(stats)}</span>'
              f'</div>')

    # 現在地
    now_text = led.track_now(2)
    now_html = ""
    if now_text:
        now_html = (f'<div class="track-section section-now">'
                    f'<div class="section-label">▶️ 現在地</div>'
                    f'<div class="section-text">{_simple_html(now_text)}</div>'
                    f'</div>')

    # Blocked
    blocked_text = led.blockers(1)
    blocked_html = ""
    if blocked_text:
        blocked_html = (f'<div class="track-section section-blocked">'
                        f'<div class="section-label">⛔ Blocked</div>'
                        f'<div class="section-text">{_simple_html(blocked_text)}</div>'
                        f'</div>')

    # セッション履歴
    if last3:
        rows_html = "".join(_session_row_html(e) for e in last3)
        more = f' <span style="color:var(--muted);font-size:11px">（全{count}S）</span>' if count > 3 else ""
        history_html = (f'<div class="track-history">'
                        f'<div class="history-label">📜 直近の討伐{more}</div>'
                        f'{rows_html}</div>')
    else:
        history_html = '<div class="track-history"><div class="history-label">（記録なし）</div></div>'

    return f'<div class="track-card">{header}{now_html}{blocked_html}{history_html}</div>'


# ── ビルド ────────────────────────────────────────────────────
def build(open_browser: bool = True) -> Path:
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    ws_labels = cfg.get("workspace_labels", {})
    mapper = ProjectMapper(cfg)

    # 1. メモリ台帳（既存の load_ledgers）
    ledgers = load_ledgers(claude_dir, ws_labels, mapper)

    # 2. project_dirs（OneDriveのprojects/等）
    for d_cfg in cfg.get("project_dirs", []):
        if isinstance(d_cfg, dict):
            d, label = d_cfg.get("path", ""), d_cfg.get("label", "プロジェクト")
        else:
            d, label = str(d_cfg), "プロジェクト"
        ledgers += load_project_dirs([d], "ext-proj", label, mapper)

    # 3. extra_track_files（gitリポジトリの現在地ファイル等）
    for f_cfg in cfg.get("extra_track_files", []):
        if isinstance(f_cfg, dict):
            raw, label = f_cfg.get("path", ""), f_cfg.get("label", "外部")
        else:
            raw, label = str(f_cfg), "外部"
        f = Path(raw).expanduser()
        if f.is_file():
            led = load_track_file(f, "ext-file", label, mapper)
            if led:
                ledgers.append(led)

    # セッション履歴があるものだけ対象
    with_hist = [l for l in ledgers if l.session_count() > 0]

    # 案件名でデdup（セッション数が多い方を優先）
    by_name: dict = {}
    for l in with_hist:
        key = (l.project or l.name or l.path.stem).strip()
        existing = by_name.get(key)
        if not existing or l.session_count() > existing.session_count():
            by_name[key] = l
    tracks = list(by_name.values())

    # 最終セッション日が新しい順にソート
    tracks.sort(key=lambda l: l.latest_session_date() or "0", reverse=True)

    # HTML生成
    body_parts = []
    body_parts.append(
        f'<h2>🏆 トラック戦績ボード<span class="n">{len(tracks)} トラック</span></h2>')
    body_parts.append('<div class="track-grid">')
    body_parts.append("".join(_track_card_html(l) for l in tracks))
    body_parts.append('</div>')

    # 戦績なし（type:project だがセッション履歴ゼロ）
    no_hist = [l for l in ledgers if l.session_count() == 0 and l.project
               and l not in tracks]
    # dedup by name
    no_hist_names = {(l.project or l.name or l.path.stem) for l in tracks}
    no_hist = [l for l in no_hist
               if (l.project or l.name or l.path.stem) not in no_hist_names]
    if no_hist:
        items = "".join(
            f'<span class="no-hist-item">{html_mod.escape(l.label)}</span>'
            for l in sorted(no_hist, key=lambda l: l.label))
        body_parts.append(
            f'<h2>📁 戦績なし<span class="n">セッション履歴が見つからない台帳 {len(no_hist)}本</span></h2>'
            f'<div class="no-hist">{items}</div>')

    total_s = sum(l.session_count() for l in tracks)
    body_parts.append(
        f'<footer>台帳 {len(ledgers)}本を走査 → 戦績ありトラック {len(tracks)}本 / '
        f'合計 {total_s}セッション。'
        f'CLI: <code>python track_record_gen.py [--no-open]</code></footer>')

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "track_record.html"
    out.write_text(_page("🏆 トラック戦績ボード", "".join(body_parts)), encoding="utf-8")

    print(f"✅ {out}  （{len(tracks)} トラック / {total_s}S）")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    build(open_browser="--no-open" not in sys.argv)


if __name__ == "__main__":
    main()
