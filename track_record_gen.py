"""トラックレコード: 各案件の「今」と「あゆみ」を1画面に。

3点セットの「あゆみ」担当。
  ボード=いま（live_board.html）  アクティビティ=ぜんたい（claude_dashboard.html）
  トラックレコード=あゆみ（track_record.html）← このスクリプト

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
from datetime import datetime, timezone
from pathlib import Path

from claude_sv.ledger import (
    load_extra_files, load_ledgers, load_project_dirs, load_track_file,
    parse_frontmatter, slug,
)
from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.mdlite import render as md_render
from claude_sv.modelbadge import MDL_CSS, model_badge
from claude_sv.nav import nav_html
from claude_sv.scan import scan_all

REPO_ROOT = Path(__file__).parent
OUT_DIR = REPO_ROOT / "reports"
TODAY = datetime.now().date()
TODAY_ISO = TODAY.isoformat()
# 実活動日・モデルに使う match_source＝採番合言葉（起動プロンプト由来）だけ。
# title-hint / workspace は粒度が粗く別トラックの活動を吸うので使わない（S12 方針・S19 で明文化）。
KICKOFF_SOURCES = {"ai-title", "first-prompts", "last-prompt"}
# 期限（状態ブロック）を「近い」と見せる日数
DUE_SOON_DAYS = 3


def _hist(led) -> list:
    """記録済みの履歴行＝日付あり・今日以前。未来日付の予定行（「2026-09-28〜」）は _planned へ。"""
    return [e for e in led.session_history() if not e.is_reserved and e.date <= TODAY_ISO]


def _planned(led) -> list:
    """予定行（日付が未来）。📅 で見せる。これを「最終日」に使うと「-3日前」になる（不具合①）。"""
    return [e for e in led.session_history() if not e.is_reserved and e.date > TODAY_ISO]


def _ledger_latest(led) -> str:
    """台帳ベースの最終日＝行順に依存しない max・期間は終端・未来は今日で丸め（不具合①⑥）。"""
    return led.latest_session_date(TODAY_ISO)


def _latest_entry(led):
    """記録済み行の中で最も新しい1行（⏳ を付ける対象＝不具合⑤）。"""
    hist = _hist(led)
    return max(hist, key=lambda e: (e.last_date, e.n or 0)) if hist else None


def _is_archived(led) -> bool:
    """状態ブロックの「状態: アーカイブ」だけがアーカイブ。日付では絶対に隠さない（S19 確定）。"""
    v = (led.state or {}).get("状態", "")
    return v.startswith("アーカイブ") or v.casefold().startswith("archiv")


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
    if delta < 0:   # 保険: 未来日付は「予定」（「-3日前」を二度と出さない）
        return f"📅 {latest_date[5:]} 予定", "status-none"
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
.tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
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
.status-unrec{color:var(--warm);border:1px dashed var(--warm)}
.track-stats{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}
.track-loc{color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums;white-space:nowrap}
.track-loc s{text-decoration:none;opacity:.7}
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
.s-row.pending .s-num{color:var(--warm)}
.s-row.open .s-num{color:var(--muted)}
.s-row.planned{opacity:.75}
.s-row.planned .s-num{color:var(--now)}
/* 全トラック一覧（表）S19 */
.tr-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:11px}
table.tr-table{border-collapse:collapse;width:100%;font-size:12px}
.tr-table th,.tr-table td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
.tr-table th{font-size:11px;color:var(--muted);font-weight:600;white-space:nowrap;cursor:pointer;
  user-select:none;background:var(--wash);position:sticky;top:0}
.tr-table th:hover{color:var(--ink)}
.tr-table th.asc::after{content:" ▲";font-size:9px}
.tr-table th.desc::after{content:" ▼";font-size:9px}
.tr-table tr:last-child td{border-bottom:none}
.tr-table td.name{white-space:nowrap;font-weight:600}
.tr-table td.name a{color:var(--ink);text-decoration:none}
.tr-table td.name a:hover{color:var(--cat-1);text-decoration:underline}
.tr-table td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tr-table td.st,.tr-table td.due,.tr-table td.upd{white-space:nowrap}
.tr-table td.txt{color:var(--ink2);max-width:360px}
.tr-table tr.arch{display:none;opacity:.55}
.tr-table.show-arch tr.arch{display:table-row}
.fb{color:var(--muted);font-style:italic}
.warn{color:var(--warm)}
.due-over{color:var(--blocked);font-weight:600}
.due-soon{color:var(--warm);font-weight:600}
.arch-toggle{margin-left:auto;font-weight:400;color:var(--muted);font-size:11px;cursor:pointer;
  display:flex;align-items:center;gap:5px}
.state-list{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:12px;color:var(--ink2)}
.state-list dt{color:var(--muted);margin:0}
.state-list dd{margin:0}
.state-list dd strong{color:var(--ink)}
/* 記録なし（既定は折りたたみ） */
.no-hist{display:flex;flex-wrap:wrap;gap:8px;padding:8px 0}
.no-hist-item{color:var(--muted);font-size:11.5px;background:var(--wash);
  border-radius:6px;padding:3px 10px}
details.no-hist-fold{margin-top:28px;border-top:1px solid var(--border);padding-top:10px}
details.no-hist-fold summary{cursor:pointer;color:var(--muted);font-size:12px;
  padding:4px 0;letter-spacing:.04em}
details.no-hist-fold summary:hover{color:var(--ink2)}
/* フッター */
footer{color:var(--muted);font-size:11px;margin-top:34px;border-top:1px solid var(--border);
  padding-top:11px}
.ws-label{color:var(--muted);font-size:10.5px;font-family:ui-monospace,Consolas,monospace;
  background:var(--wash);border-radius:4px;padding:0 5px;margin-left:4px}
/* トラック名リンク（カード→個別ページ） */
a.track-name{color:var(--ink);text-decoration:none}
a.track-name:hover{color:var(--cat-1);text-decoration:underline}
.section-next .section-label{color:var(--hot)}
/* 個別ページ */
.detail-card{max-width:900px;margin:0 auto}
.detail-head{display:flex;align-items:baseline;flex-wrap:wrap;gap:9px;margin-bottom:4px}
.detail-title{font-size:16px;font-weight:600;margin:0;color:var(--ink)}
.where{color:var(--muted);font-size:11px;font-family:ui-monospace,Consolas,monospace;margin-top:8px}
details.detail-full{border-top:1px solid var(--border);padding-top:8px;margin-top:2px}
details.detail-full summary{cursor:pointer;color:var(--muted);font-size:11.5px;padding:5px 0;list-style:revert}
/* 台帳全文（md） */
.md{color:var(--ink2);font-size:12.5px;margin-top:8px}
.md p{margin:.35em 0}
.md ul{margin:.3em 0;padding-left:1.25em}
.md li{margin:.16em 0}
.md h3,.md h4{font-size:12.5px;color:var(--ink);margin:1.1em 0 .35em}
.md code{background:var(--wash);border-radius:4px;padding:1px 5px;font-size:11.5px;
  font-family:ui-monospace,Consolas,monospace}
.md pre{background:var(--wash);border-radius:7px;padding:9px 12px;overflow-x:auto;
  font-size:11.5px;font-family:ui-monospace,Consolas,monospace}
.md a{color:var(--cat-1)}
.md .wiki{color:var(--cat-1);opacity:.85}
.md table{border-collapse:collapse;margin:.5em 0;font-size:11.5px;display:block;overflow-x:auto}
.md th,.md td{border:1px solid var(--border);padding:4px 9px;text-align:left}
.md th{background:var(--wash)}
.md hr{border:0;border-top:1px solid var(--border);margin:.8em 0}
""" + MDL_CSS


# ── ページ組み立て ─────────────────────────────────────────────
def _page(title: str, body: str, nav: str = "") -> str:
    if not nav:  # 戦績ボード（reports 直下）を現在地とする統一ナビ
        nav = nav_html("record")
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


# ── 案件名の正規化（live_board とリンクを繋ぐ肝）───────────────────
def _canonical_key(led, mapper) -> str:
    """live_board と同じ alias テーブル（_resolve_project・title_hints は使わない）で正規化。

    フル案件名は完全一致でそのまま保持し、memory台帳の stem・別名だけ canonical 名に寄せる
    （kanademono-dev → KANADEMONO開発 / KANADEMONO-ウェルカムDM → ウェルカムDM 等）。
    live_board の r["project"]（map_session の canonical）と同じテーブルに着地するので、
    slug() が一致してトラック名リンクが個別ページに繋がる。
    title_hints を使うと「GEMiNX本体開発→GEMiNX」と粒度が粗くなり衝突するので使わない。
    """
    declared = (led.state or {}).get("トラック名", "").strip()   # 台帳が自分で宣言した表示名（S19）
    if mapper is not None:
        for cand in (declared, led.project, led.name, led.path.stem):
            if cand:
                r = mapper._resolve_project(cand)
                if r and mapper._norm(r) != mapper._norm(cand):
                    return r
    return declared or led.project or led.name or led.path.stem


# ── トラック→直近セッションの model／実活動日（S14・S17）─────────
def _act_local_date(ts_iso: str) -> str:
    """scan の last_ts（UTC ISO）→ ローカル(JST)の YYYY-MM-DD。壊れていたら ""。
    UTC のまま日付を切ると朝のセッションが前日にずれるので必ずローカル変換する。"""
    if not ts_iso:
        return ""
    try:
        d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _latest_scan(cfg, mapper) -> tuple:
    """案件(canonical)→(直近セッションの model, 実活動の最終ローカル日付, 書いた行数) を jsonl scan から引く。

    scan_all はアクティビティと同じ差分キャッシュ（daily_maintenance は dashboard→ここ の順
    ＝温まった状態で走る）。採番セッションの案件名は同じ alias テーブルで canonical に落ちる
    ので _canonical_key と突合できる。いずれも飾り＝scan が死んでも本体は生成する。
    実活動日（S17）＝台帳は締め時にしか進まないので、進行中セッションの「今日動いてる」は
    こちらで補正する（A案・⚠️未記帳マーク）。
    """
    models, activity, loc = {}, {}, {}
    try:
        sessions = scan_all(
            Path(cfg.get("claude_dir", "~/.claude")).expanduser(),
            cache_path=REPO_ROOT / "cache" / "scan_cache.json",
            workspaces=cfg.get("workspaces") or None)
    except OSError:
        return models, activity, loc
    best_model_ts, best_act_ts = {}, {}
    for s in sessions:
        mapper.map_session(s)
        p, ts = s.get("project"), s.get("last_ts") or ""
        if not p or not ts:
            continue
        # 書いた行数（S18）: 案件の全期間累計 code/docs/added/removed/unmeasured
        sl = s.get("loc") or {}
        if sl:
            r = loc.setdefault(p, {"added": 0, "code": 0, "docs": 0, "removed": 0, "unmeasured": 0})
            for k in r:
                r[k] += sl.get(k, 0)
        if s.get("match_source") not in KICKOFF_SOURCES:
            continue   # title-hint/workspace 由来はトラックの活動としては使わない（誤判定防止）
        if s.get("model") and ts > best_model_ts.get(p, ""):
            best_model_ts[p] = ts
            models[p] = s["model"]
        if ts > best_act_ts.get(p, ""):
            best_act_ts[p] = ts
    activity = {p: _act_local_date(ts) for p, ts in best_act_ts.items()}
    return models, activity, loc


def _loc_stat_html(loc) -> str:
    """統計欄に添える「＋n行」（ホバーで code/docs/削除/未計測の内訳）。行数ゼロなら出さない。"""
    if not loc or not loc.get("added"):
        return ""
    tip = (f'書いた行数（全期間・成功した編集のみ）: コード {loc["code"]:,} ／ '
           f'ドキュメント {loc["docs"]:,} ／ 削除 {loc["removed"]:,} ／ 未計測の編集 {loc["unmeasured"]}件')
    return (f'<span class="track-loc" title="{html_mod.escape(tip)}">'
            f'＋{loc["added"]:,}行 <s>{loc["code"]:,}code</s></span>')


# ── カード HTML ───────────────────────────────────────────────
def _track_slug(led) -> str:
    """個別ページのファイル名・カードのアンカー id に使うスラッグ。
    live_board.py も同じ slug() で `r["project"]` から同一スラッグを組む（一致すればリンクが繋がる）。
    led.project は build() で canonical に揃え済み。"""
    return slug((led.project or led.name or led.path.stem).strip())


def _session_row_html(entry, sum_len: int = 55, is_latest: bool = False, planned: bool = False) -> str:
    """履歴1行。アイコン: 📅予定（未来日付）／✅完了／⏳進行中（結果空欄でも最新行だけ）／◦記録のみ。
    以前は結果列が空欄なら全部 ⏳ だった（不具合⑤）。"""
    # 期間行（「08-29〜09-25」）は終端＝その行の最終日を出す（ツールチップに全期間）。不具合⑥
    date_short = entry.last_date[5:] if len(entry.last_date) >= 10 else entry.last_date  # "09-25"
    if planned:
        icon, row_cls = "📅", "planned"
    elif entry.is_done:
        icon, row_cls = "✅", "done"
    elif is_latest:
        icon, row_cls = "⏳", "pending"
    else:
        icon, row_cls = "◦", "open"
    date_title = f"{entry.date}〜{entry.date_end}" if entry.date_end else entry.date
    summary = entry.summary[:sum_len] + ("…" if len(entry.summary) > sum_len else "")
    result_text = re.sub(r"✅\s*", "", entry.result)[:35] if entry.result else "—"
    return (f'<div class="s-row {row_cls}">'
            f'<span class="s-icon">{icon}</span>'
            f'<span class="s-num">{html_mod.escape(entry.session)}</span>'
            f'<span class="s-date" title="{date_title}">{date_short}</span>'
            f'<span class="s-sum" title="{html_mod.escape(entry.result)}">'
            f'{html_mod.escape(summary)}</span>'
            f'<span class="s-result">{html_mod.escape(result_text)}</span>'
            f'</div>')


UNREC_RECENT_DAYS = 3  # ⚠️未記帳を出すのは実活動が直近この日数以内の時だけ（古い乖離=対処不能ノイズ）


def _unrec_badge(latest: str, act_date: str) -> str:
    """台帳より実活動(jsonl)が新しい＝進行中/締め忘れセッションの ⚠️未記帳 バッジ。
    昔の乖離（翌朝跨ぎ等の1日ズレ痕跡）まで出すと狼少年になるので直近分に限定する。"""
    if not (act_date and act_date > (latest or "")):
        return ""
    try:
        age = (TODAY - datetime.strptime(act_date, "%Y-%m-%d").date()).days
    except ValueError:
        return ""
    if age > UNREC_RECENT_DAYS:
        return ""
    return (f'<span class="status-badge status-unrec" title="実活動 {act_date[5:]} ＞ '
            f'台帳 {latest[5:] if latest else "—"}（セッション未記帳＝進行中か締め忘れ）">'
            f'⚠️ 未記帳</span>')


def _history_rows_html(led, limit: int | None, sum_len: int) -> str:
    """予定行（📅・新しい順）→ 記録済み行（新しい順・limit 件）。⏳ は最新の未完了行だけ。"""
    hist = sorted(_hist(led), key=lambda e: (e.last_date, e.n or 0))
    latest = hist[-1] if hist else None
    shown = list(reversed(hist)) if limit is None else list(reversed(hist))[:limit]
    planned = sorted(_planned(led), key=lambda e: (e.date, e.n or 0), reverse=True)
    return ("".join(_session_row_html(e, sum_len, planned=True) for e in planned)
            + "".join(_session_row_html(e, sum_len, is_latest=(e is latest)) for e in shown))


def _track_card_html(led, model=None, act_date="", loc=None) -> str:
    hist = _hist(led)
    count = len(hist)
    since = led.active_since()
    latest = _ledger_latest(led)

    # バッジの鮮度は max(台帳の最終行, jsonl実活動)（S17 A案）。統計欄は台帳ベース＝正本のまま
    eff = max((d for d in (latest, act_date) if d), default="")
    status_label, status_cls = _activity_status(eff)

    # 統計テキスト
    stats_parts = [f"累計 {count}S"]
    if since:
        stats_parts.append(since[5:])  # "07-29"
        if latest and latest != since:
            stats_parts.append(f"〜 {latest[5:]}")
    stats = " | ".join(stats_parts)

    # ソース表示（ws_label）
    ws_span = f'<span class="ws-label">{html_mod.escape(led.ws_label)}</span>' if led.ws_label else ""

    # ヘッダー（トラック名は個別ページへのリンク）
    tslug = _track_slug(led)
    header = (f'<div class="track-header">'
              f'<a class="track-name" href="track/{tslug}.html">{html_mod.escape(led.label)}</a>{ws_span}'
              f'<span class="status-badge {status_cls}">{status_label}</span>'
              f'{_unrec_badge(latest, act_date)}'
              f'{model_badge(model, note="直近セッションのモデル")}'
              f'<span class="track-stats">{html_mod.escape(stats)}</span>{_loc_stat_html(loc)}'
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

    # セッション履歴（予定 📅 ＋ 直近3件）
    if hist or _planned(led):
        rows_html = _history_rows_html(led, 3, 55)
        more = f' <span style="color:var(--muted);font-size:11px">（全{count}S）</span>' if count > 3 else ""
        history_html = (f'<div class="track-history">'
                        f'<div class="history-label">📜 直近の討伐{more}</div>'
                        f'{rows_html}</div>')
    else:
        history_html = '<div class="track-history"><div class="history-label">（記録なし）</div></div>'

    return (f'<div class="track-card" id="track-{tslug}">'
            f'{header}{now_html}{blocked_html}{history_html}</div>')


# ── 個別ページ HTML ───────────────────────────────────────────
def _detail_page(led, model=None, act_date="", loc=None) -> str:
    """1トラック=1ページの詳細（ライブボードのトラック名リンク先）。
    上部＝構造化サマリ（現在地/Next/Blocked/全セッション履歴）、下部＝台帳全文（折りたたみ）。"""
    label = led.label
    latest = _ledger_latest(led)
    eff = max((d for d in (latest, act_date) if d), default="")
    status_label, status_cls = _activity_status(eff)
    hist = _hist(led)
    count = len(hist)
    since = led.active_since()

    stats_parts = [f"累計 {count}S"]
    if since:
        stats_parts.append(since[5:])
        if latest and latest != since:
            stats_parts.append(f"〜 {latest[5:]}")
    stats = " | ".join(stats_parts)
    ws_span = (f'<span class="ws-label">{html_mod.escape(led.ws_label)}</span>'
               if led.ws_label else "")

    header = (f'<div class="detail-head">'
              f'<h1 class="detail-title">🗂️ {html_mod.escape(label)}</h1>{ws_span}'
              f'<span class="status-badge {status_cls}">{status_label}</span>'
              f'{_unrec_badge(latest, act_date)}'
              f'{model_badge(model, note="直近セッションのモデル")}'
              f'<span class="track-stats">{html_mod.escape(stats)}</span>{_loc_stat_html(loc)}</div>')

    def section(lbl, cls, text):
        if not text:
            return ""
        return (f'<div class="track-section {cls}">'
                f'<div class="section-label">{lbl}</div>'
                f'<div class="section-text">{_simple_html(text)}</div></div>')
    state_html = _state_section_html(led, latest)
    now_html = section("▶️ 現在地", "section-now", led.track_now(4))
    next_html = section("▶️ Next", "section-next", led.next_up(3))
    blocked_html = section("⛔ Blocked", "section-blocked", led.blockers(2))

    # セッション履歴（予定 📅 ＋ 全件・新しい順）
    if hist or _planned(led):
        rows = _history_rows_html(led, None, 90)
        history_html = (f'<div class="track-history">'
                        f'<div class="history-label">📜 セッション履歴（全{count}S）</div>'
                        f'{rows}</div>')
    else:
        history_html = ('<div class="track-history">'
                        '<div class="history-label">（セッション履歴なし）</div></div>')

    # 台帳全文（折りたたみ）
    try:
        full = parse_frontmatter(led.path.read_text(encoding="utf-8"))[1]
    except (OSError, UnicodeDecodeError):
        full = ""
    full_html = ""
    if full.strip():
        full_html = (f'<details class="detail-full"><summary>📄 台帳全文を開く</summary>'
                     f'<div class="md">{md_render(full)}</div>'
                     f'<div class="where">正本: {html_mod.escape(str(led.path))}</div>'
                     f'</details>')

    nav = nav_html("", rel="../")   # 個別ページ＝3点全部リンク（ボード=同一タブ / マップ・戦績=新タブ）
    body = (f'<div class="track-card detail-card">{header}{state_html}{now_html}{next_html}'
            f'{blocked_html}{history_html}{full_html}</div>')
    return _page(f"🗂️ {label}", body, nav=nav)


# ── 状態ブロック（S19）────────────────────────────────────────
STATE_KEYS = ("状態", "現S", "次の一手", "待ち", "期限", "更新")


def _state_stale(led, latest: str) -> str:
    """状態ブロックの 更新 が台帳の最終セッション日より古ければ警告文（無ければ ""）。"""
    upd = (led.state or {}).get("更新", "")
    m = re.search(r"\d{4}-\d{2}-\d{2}", upd)
    if m and latest and m.group() < latest:
        return f"⚠️ 状態ブロック {m.group()[5:]} ＜ 台帳 {latest[5:]}（更新漏れ）"
    return ""


def _due_class(due_raw: str) -> tuple:
    """期限セルの (表示, class)。今日を過ぎたら赤、DUE_SOON_DAYS 以内は黄。日付が読めなければそのまま。"""
    m = re.search(r"\d{4}-\d{2}-\d{2}", due_raw or "")
    if not m:
        return (due_raw or "—"), ""
    try:
        delta = (datetime.strptime(m.group(), "%Y-%m-%d").date() - TODAY).days
    except ValueError:
        return due_raw, ""
    if delta < 0:
        return f"{due_raw}（{-delta}日超過）", "due-over"
    if delta <= DUE_SOON_DAYS:
        return f"{due_raw}（あと{delta}日）", "due-soon"
    return due_raw, ""


def _state_section_html(led, latest: str) -> str:
    """個別ページの 📌 状態ブロック（無ければ ⚠️ で「無い」ことを見せる）。"""
    st = led.state or {}
    if not st:
        return ('<div class="track-section"><div class="section-label warn">📌 状態ブロックなし</div>'
                '<div class="section-text fb">台帳先頭の <code>&lt;!-- track-state --&gt;</code> が未設置。'
                '一覧表は台帳の Next/Blocked から推測表示中</div></div>')
    items = []
    for k in STATE_KEYS + tuple(x for x in st if x not in STATE_KEYS and x != "トラック名"):
        v = st.get(k, "")
        if not v:
            continue
        if k == "期限":
            v_html, cls = _due_class(v)
            v_html = f'<span class="{cls}">{html_mod.escape(v_html)}</span>' if cls else html_mod.escape(v_html)
        else:
            v_html = _simple_html(v)
        items.append(f'<dt>{html_mod.escape(k)}</dt><dd>{v_html}</dd>')
    stale = _state_stale(led, latest)
    stale_html = f'<div class="section-text warn">{html_mod.escape(stale)}</div>' if stale else ""
    return (f'<div class="track-section"><div class="section-label">📌 状態ブロック（台帳先頭・一次ソース）</div>'
            f'<dl class="state-list">{"".join(items)}</dl>{stale_html}</div>')


# ── 全トラック一覧（表）S19 ──────────────────────────────────
TABLE_JS = """(function(){
  var tb=document.getElementById('trt'); if(!tb) return;
  var chk=document.getElementById('show-arch');
  if(chk){chk.onchange=function(){tb.classList.toggle('show-arch',chk.checked);};}
  var ths=tb.querySelectorAll('th[data-k]');
  ths.forEach(function(th){th.onclick=function(){
    var k=+th.dataset.k, asc=!th.classList.contains('asc');
    ths.forEach(function(t){t.classList.remove('asc','desc');});
    th.classList.add(asc?'asc':'desc');
    var body=tb.tBodies[0], rows=Array.prototype.slice.call(body.rows);
    rows.sort(function(a,b){var x=a.cells[k].dataset.v||'',y=b.cells[k].dataset.v||'';
      return (x<y?-1:x>y?1:0)*(asc?1:-1);});
    rows.forEach(function(r){body.appendChild(r);});
  };});
})();"""


def _table_html(tracks, eff_of, act_of) -> str:
    """全トラック1行ずつ。一次ソース＝状態ブロック。無い列は台帳から推測して斜体（fb）で埋め、
    状態列に ⚠️ を出す＝書き忘れを機械が知らせる。アーカイブ行は既定で非表示（トグルで可逆）。"""
    esc = html_mod.escape
    head = "".join(
        f'<th data-k="{i}">{h}</th>' for i, h in enumerate(
            ("トラック名", "状態・現S", "次の一手", "待ち", "期限", "最終更新", "累計S")))
    rows = []
    for led in tracks:
        st = led.state or {}
        latest = _ledger_latest(led)
        le = _latest_entry(led)
        count = len(_hist(led))
        arch = _is_archived(led)

        # 状態・現S
        if st:
            st_txt = " ".join(x for x in (st.get("状態", ""), st.get("現S", "")) if x)
            st_html = _simple_html(st_txt) if st_txt else '<span class="warn">⚠️ 状態/現S 未記入</span>'
            stale = _state_stale(led, latest)
            if stale:
                st_html += f'<br><span class="warn" style="font-size:11px">{esc(stale)}</span>'
            st_v = "1-" + st_txt
        else:
            fb = f"台帳: {le.session}{' ✅' if le.is_done and '✅' not in le.session else ''}" if le else ""
            st_html = ('<span class="warn" title="台帳先頭に &lt;!-- track-state --&gt; が無い">⚠️ 状態ブロックなし</span>'
                       + (f'<br><span class="fb">{esc(fb)}</span>' if fb else ""))
            st_v = "9-"

        # 次の一手 / 待ち（状態ブロック → 台帳 Next/Blocked 推測）
        def _txt(key, fallback):
            v = st.get(key, "")
            if v:
                return _simple_html(v), v
            fbv = (fallback or "").strip().splitlines()
            fbv = fbv[0] if fbv else ""
            return (f'<span class="fb" title="台帳の見出しから推測">{_simple_html(fbv)}</span>' if fbv else "—"), fbv
        next_html, next_v = _txt("次の一手", led.next_up(1))
        wait_html, wait_v = _txt("待ち", led.blockers(1))

        # 期限
        due_raw = st.get("期限", "")
        due_txt, due_cls = _due_class(due_raw)
        due_html = f'<span class="{due_cls}">{esc(due_txt)}</span>' if due_cls else esc(due_txt)
        dm = re.search(r"\d{4}-\d{2}-\d{2}", due_raw)
        due_v = dm.group() if dm else "9999"

        # 最終更新（実効日＝max(台帳, 実活動)）
        eff = eff_of(led)
        lab, cls = _activity_status(eff)
        upd_html = (f'<span class="status-badge {cls}">{lab}</span>{_unrec_badge(latest, act_of(led))}')

        tslug = slug((led.project or led.name or led.path.stem).strip())
        ws = f'<span class="ws-label">{esc(led.ws_label)}</span>' if led.ws_label else ""
        rows.append(
            f'<tr class="{"arch" if arch else ""}">'
            f'<td class="name" data-v="{esc(led.label)}"><a href="track/{tslug}.html">{esc(led.label)}</a>{ws}</td>'
            f'<td class="st" data-v="{esc(st_v)}">{st_html}</td>'
            f'<td class="txt" data-v="{esc(next_v)}">{next_html}</td>'
            f'<td class="txt" data-v="{esc(wait_v)}">{wait_html}</td>'
            f'<td class="due" data-v="{due_v}">{due_html}</td>'
            f'<td class="upd" data-v="{eff or "0"}">{upd_html}</td>'
            f'<td class="num" data-v="{count:04d}">{count}</td>'
            f'</tr>')
    n_arch = sum(1 for l in tracks if _is_archived(l))
    toggle = (f'<label class="arch-toggle"><input type="checkbox" id="show-arch"> '
              f'アーカイブ {n_arch}本 も表示</label>')
    return (f'<h2>📋 全トラック一覧<span class="n">{len(tracks) - n_arch} トラック'
            f'（列見出しクリックで並べ替え）</span>{toggle}</h2>'
            f'<div class="tr-wrap"><table class="tr-table" id="trt"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div><script>{TABLE_JS}</script>')


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

    # live_board と同じ正規化でトラック名を canonical に揃える（リンク繋ぎ＋表示統一）。
    # memory台帳の stem（kanademono-dev 等）を KANADEMONO開発 等の正規名に寄せる。
    for l in ledgers:
        l.project = _canonical_key(l, mapper)

    # 対象＝セッション履歴があるもの ＋ 状態ブロックを宣言しているもの（S19）
    with_hist = [l for l in ledgers if l.session_history() or l.state]

    # 案件名でデdup（セッション数が多い方を優先・同数なら状態ブロック持ちを優先）
    by_name: dict = {}
    for l in with_hist:
        key = (l.project or l.name or l.path.stem).strip()
        existing = by_name.get(key)
        if (not existing
                or (l.session_count(), bool(l.state)) > (existing.session_count(), bool(existing.state))):
            if existing and existing.state and not l.state:
                l.state = existing.state   # 負けた台帳側にだけ状態ブロックがある → 勝った方へ引き継ぐ
            by_name[key] = l
        elif l.state and not existing.state:
            existing.state = l.state       # 同一トラックの別ファイル（projects/ と memory/）に書かれた状態ブロックを拾う
    all_tracks = list(by_name.values())
    # カード（帯）にはアーカイブ以外。表には全部（アーカイブ行は既定で非表示・トグルで戻る）
    tracks = [l for l in all_tracks if not _is_archived(l)]
    archived = [l for l in all_tracks if _is_archived(l)]

    # トラック→直近セッションの model／実活動日（jsonl scan・キャッシュ利用）
    models, activity, locs = _latest_scan(cfg, mapper)

    def _key_of(led):
        return (led.project or led.name or led.path.stem).strip()

    def _model_of(led):
        return models.get(_key_of(led))

    def _act_of(led):
        return activity.get(_key_of(led), "")

    def _loc_of(led):
        return locs.get(_key_of(led))

    def _eff_date(led) -> str:
        """バッジ・帯判定の実効日＝max(台帳の最終行, jsonl実活動)（S17 A案）。"""
        return max((d for d in (_ledger_latest(led), _act_of(led)) if d),
                   default="")

    # 並び順（S17 ふくちゃん決定）: バッジ帯（🟢≤7日/🟡≤30日/⚫それ以前）でグループ化し、
    # 帯内は累計S数の多い順＝「今燃えてる歴戦トラック」が先頭。同S数は直近日順（安定ソート2段）。
    def _band(led) -> int:
        d = _eff_date(led)
        if d:
            try:
                delta = (TODAY - datetime.strptime(d, "%Y-%m-%d").date()).days
            except ValueError:
                return 3
            return 0 if delta <= 7 else (1 if delta <= 30 else 2)
        return 3

    tracks.sort(key=lambda l: _eff_date(l) or "0", reverse=True)
    tracks.sort(key=lambda l: (_band(l), -l.session_count()))
    archived.sort(key=lambda l: _eff_date(l) or "0", reverse=True)

    # HTML生成: 先頭に全トラック一覧（表）、その下に帯ごとのカード
    body_parts = [_table_html(tracks + archived, _eff_date, _act_of)]
    BANDS = [(0, "🟢 直近7日"), (1, "🟡 30日以内"), (2, "⚫ 休眠"), (3, "— 日付なし")]
    for b, label in BANDS:
        subset = [l for l in tracks if _band(l) == b]
        if not subset:
            continue
        body_parts.append(
            f'<h2>{label}<span class="n">{len(subset)} トラック</span></h2>')
        body_parts.append(
            '<div class="track-grid">'
            + "".join(_track_card_html(l, _model_of(l), _act_of(l), _loc_of(l)) for l in subset)
            + '</div>')

    # 戦績なし（type:project だがセッション履歴ゼロ・状態ブロックもなし）
    no_hist = [l for l in ledgers if not l.session_history() and not l.state and l.project
               and l not in all_tracks]
    # dedup by name
    no_hist_names = {(l.project or l.name or l.path.stem) for l in all_tracks}
    no_hist = [l for l in no_hist
               if (l.project or l.name or l.path.stem) not in no_hist_names]
    if no_hist:
        items = "".join(
            f'<span class="no-hist-item">{html_mod.escape(l.label)}</span>'
            for l in sorted(no_hist, key=lambda l: l.label))
        # 既定は畳む（S17 ふくちゃん指示のノイズ削減。件数だけ見せて中身はワンクリック）
        body_parts.append(
            f'<details class="no-hist-fold"><summary>📁 記録なし — '
            f'セッション履歴が見つからない台帳 {len(no_hist)}本</summary>'
            f'<div class="no-hist">{items}</div></details>')

    total_s = sum(l.session_count() for l in tracks)
    n_state = sum(1 for l in all_tracks if l.state)
    body_parts.append(
        f'<footer>台帳 {len(ledgers)}本を走査 → トラック {len(tracks)}本（＋アーカイブ {len(archived)}本）/ '
        f'合計 {total_s}セッション / 状態ブロックあり {n_state}本。'
        f'アーカイブは台帳の状態ブロック「状態: アーカイブ」だけで決まる（日付では隠さない）。'
        f'CLI: <code>python track_record_gen.py [--no-open]</code></footer>')

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "track_record.html"
    out.write_text(_page("🏆 トラックレコード", "".join(body_parts)), encoding="utf-8")

    # 個別ページ（1トラック1枚＝ライブボードのトラック名リンク先 reports/track/<slug>.html）
    track_dir = OUT_DIR / "track"
    track_dir.mkdir(exist_ok=True)
    for old in track_dir.glob("*.html"):   # 前回の stale（canonical 変更・トラック消滅・改名）を掃除
        old.unlink()
    seen = set()
    for led in tracks + archived:   # アーカイブも個別ページは持つ（表のトグルから辿れる）
        fname = _track_slug(led) + ".html"
        if fname in seen:   # by_name で案件名は一意だが 60字 truncate 衝突の保険（先勝ち）
            continue
        seen.add(fname)
        (track_dir / fname).write_text(
            _detail_page(led, _model_of(led), _act_of(led), _loc_of(led)), encoding="utf-8")

    print(f"✅ {out}  （{len(tracks)} トラック＋アーカイブ {len(archived)} / {total_s}S / "
          f"状態ブロック {n_state}本 / 個別ページ {len(seen)}枚）")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    build(open_browser="--no-open" not in sys.argv)


if __name__ == "__main__":
    main()
