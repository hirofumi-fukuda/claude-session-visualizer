"""L1 静的ダッシュボード: 案件×セッション地図・稼働リズム・トークンを HTML 1枚に生成する。

使い方:  python claude_dashboard_gen.py
出力:    reports/claude_dashboard.html      … ブラウザで開く1枚もの（gitignore対象・ローカルのみ）
         reports/claude_dashboard_data.json … 中間データ（L3 業務ダッシュボード統合用）

生成物にトランスクリプト本文（発話・応答テキスト）は含めない（docs/01-設計方針.md）。
未分類セッションの識別は ai-title のみ。本文スニペットが必要なら l0_report.py を使う。

デザイン（S8 2026-08-01 全面刷新・ダーク既定＋ライト切替）:
  - 外部リソース参照ゼロを維持（CDN禁止・SVGは自前生成）。色は全て CSS 変数＝テーマ切替が1箇所で効く
  - 色は「識別」か「量」にだけ使う。statタイルの数字は必ずインク色（データ色を着せない）
  - 量のスケールは単一色相の6段ランプ（seq-1..6）。ダーク時は暗→明、ライト時は明→暗＝
    ゼロが常に地の色へ沈む向き。ダーク面での緑αは状態色（🟢作業中）と衝突するので使わない
  - どのグラフも「表で見る」を併設（色だけに意味を載せない）
"""
import html
import io
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from claude_sv.ledger import slug
from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.modelbadge import MDL_CSS, model_badge, shorten
from claude_sv.nav import nav_html
from claude_sv.scan import scan_all

REPO_ROOT = Path(__file__).parent
HEATMAP_DAYS = 14
HEATMAP_MAX_ROWS = 12
TREND_DAYS = 30       # 日別推移グラフの表示日数
SPARK_DAYS = 12       # ヒーロー数字に添えるスパークラインの点数
RECENT_OPEN_DAYS = 7  # この日数以内に稼働した案件は地図を開いた状態で表示
TOKEN_BAR_ROWS = 15
UNCLASSIFIED = "（未分類）"
WD_LABELS = ["月", "火", "水", "木", "金", "土", "日"]
SRC_LABELS = {"ai-title": "採番（AIタイトル）", "first-prompts": "採番（冒頭発話）",
              "last-prompt": "採番（最終発話）", "title-hint": "キーワード", "workspace": "ワークスペース"}

esc = html.escape


def _dt(ts_iso):
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _fmt(ts_iso, fmt="%m/%d %H:%M"):
    dt = _dt(ts_iso)
    return dt.strftime(fmt) if dt else "?"


def _span(first_ts, last_ts):
    """期間表示: 同日なら「07/29 09:10〜15:42」、別日なら日付付き。"""
    a, b = _dt(first_ts), _dt(last_ts)
    if not a or not b:
        return "?"
    if a.date() == b.date():
        return f"{a.strftime('%m/%d %H:%M')}〜{b.strftime('%H:%M')}"
    return f"{a.strftime('%m/%d %H:%M')}〜{b.strftime('%m/%d %H:%M')}"


def _k(n):
    """トークン等の桁圧縮（1,234,567 → 1,235k）。"""
    return f"{n / 1000:,.0f}k" if n >= 1000 else f"{n:,}"


def _bin(v, peak, n=6, curve=0.5):
    """量 → 1..n のランプ段。0 は 0（地の色）。偏りが強いので既定は sqrt スケール。"""
    if not v or peak <= 0:
        return 0
    return max(1, min(n, int((v / peak) ** curve * n + 0.999)))


# ---- データ集計（JSON 中間出力＝L3 統合用。テキスト本文は入れない） ----

def build_data(sessions, cfg):
    ws_labels = cfg.get("workspace_labels", {})
    matched = [s for s in sessions if s["project"]]
    unmatched = [s for s in sessions if not s["project"]]

    # 案件×S番号で束ねる（/clear 分割で同一Sが複数ファイルに分かれるため）。S なしは1ファイル=1行
    bundles = {}
    for s in matched:
        key = (s["project"],
               s["session_no"] if s["session_no"] is not None else f"f:{s['session_id']}")
        b = bundles.setdefault(key, {
            "session_no": s["session_no"], "n_files": 0,
            "first_ts": None, "last_ts": None,
            "n_user_prompts": 0, "n_assistant": 0, "n_tool_use": 0, "n_subagents": 0,
            "output_tokens": 0, "workspaces": set(), "models": set(),
            "loc_added": 0, "loc_code": 0, "loc_docs": 0, "loc_removed": 0,
        })
        b["n_files"] += 1
        loc = s.get("loc") or {}
        b["loc_added"] += loc.get("added", 0)
        b["loc_code"] += loc.get("code", 0)
        b["loc_docs"] += loc.get("docs", 0)
        b["loc_removed"] += loc.get("removed", 0)
        if s["first_ts"] and (b["first_ts"] is None or s["first_ts"] < b["first_ts"]):
            b["first_ts"] = s["first_ts"]
        if s["last_ts"] and (b["last_ts"] is None or s["last_ts"] > b["last_ts"]):
            b["last_ts"] = s["last_ts"]
        for k in ("n_user_prompts", "n_assistant", "n_tool_use", "output_tokens"):
            b[k] += s[k]
        b["n_subagents"] += s.get("n_subagents", 0)
        b["workspaces"].add(ws_labels.get(s["workspace"], s["workspace"]))
        if s.get("model"):  # /clear 分割の各ファイルの最終モデル（普段は1つ・切替セッションは複数）
            b["models"].add(shorten(s["model"]))

    by_project = defaultdict(list)
    for (proj, _), b in bundles.items():
        b["workspaces"] = sorted(b["workspaces"])
        b["models"] = sorted(b["models"])
        by_project[proj].append(b)

    projects = []
    for name, blist in by_project.items():
        blist.sort(key=lambda b: (b["session_no"] is None, b["session_no"] or 0,
                                  b["first_ts"] or ""))
        latest_b = max(blist, key=lambda b: b["last_ts"] or "")
        projects.append({
            "name": name,
            "last_ts": max(b["last_ts"] or "" for b in blist),
            "n_bundles": len(blist),
            "n_files": sum(b["n_files"] for b in blist),
            "output_tokens": sum(b["output_tokens"] for b in blist),
            "loc_added": sum(b["loc_added"] for b in blist),
            "model": latest_b["models"][-1] if latest_b["models"] else None,  # 直近Sのモデル
            "bundles": blist,
        })
    projects.sort(key=lambda p: p["last_ts"], reverse=True)

    # 日別稼働（直近 HEATMAP_DAYS 日 × 案件・assistant 応答数）
    today = datetime.now().astimezone().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(HEATMAP_DAYS - 1, -1, -1)]
    act = defaultdict(lambda: defaultdict(int))
    for s in sessions:
        p = s["project"] or UNCLASSIFIED
        for day, n in s["days_active"].items():
            if day in days:
                act[p][day] += n
    heat_rows = [{"project": p, "counts": [act[p][d] for d in days],
                  "total": sum(act[p].values())} for p in act]
    heat_rows.sort(key=lambda r: -r["total"])

    # 日別推移（直近 TREND_DAYS 日・出力トークンと応答数）
    tdays = [(today - timedelta(days=i)).isoformat() for i in range(TREND_DAYS - 1, -1, -1)]
    tok_by_day, msg_by_day = defaultdict(int), defaultdict(int)
    for s in sessions:
        for day, v in s.get("days_tokens", {}).items():
            tok_by_day[day] += v
        for day, v in s.get("days_active", {}).items():
            msg_by_day[day] += v
    daily = {"days": tdays,
             "tokens": [tok_by_day[d] for d in tdays],
             "assistant": [msg_by_day[d] for d in tdays]}
    last7 = sum(tok_by_day[d] for d in tdays[-7:])
    prev7 = sum(tok_by_day[d] for d in tdays[-14:-7])

    # 稼働リズム（曜日×時間帯・全期間の assistant 応答数）
    punch = [[0] * 24 for _ in range(7)]
    for s in sessions:
        for slot, n in s.get("hours_active", {}).items():
            wd, _, hr = slot.partition("-")
            try:
                punch[int(wd)][int(hr)] += n
            except (ValueError, IndexError):
                continue

    # 案件別トークン（全期間累計）
    tok = defaultdict(lambda: {"output_tokens": 0, "n_sessions": 0})
    for s in sessions:
        p = s["project"] or UNCLASSIFIED
        tok[p]["output_tokens"] += s["output_tokens"]
        tok[p]["n_sessions"] += 1
    token_rows = [{"project": p, **v} for p, v in tok.items()]
    token_rows.sort(key=lambda r: -r["output_tokens"])

    unmatched_rows = [{
        "last_ts": s["last_ts"], "ai_title": s["ai_title"],
        "n_user_prompts": s["n_user_prompts"], "size_kb": s["size_bytes"] // 1024,
        "workspace": ws_labels.get(s["workspace"], s["workspace"]),
    } for s in sorted(unmatched, key=lambda s: s["last_ts"] or "", reverse=True)]

    src_counts = defaultdict(int)
    for s in matched:
        src_counts[s["match_source"]] += 1

    loc_data = _build_loc(sessions, tdays)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {
            "sessions": len(sessions),
            "size_mb": round(sum(s["size_bytes"] for s in sessions) / 1048576, 1),
            "output_tokens": sum(s["output_tokens"] for s in sessions),
            "assistant": sum(s["n_assistant"] for s in sessions),
            "subagents": sum(s.get("n_subagents", 0) for s in sessions),
            "tool_use": sum(s["n_tool_use"] for s in sessions),
            "user_prompts": sum(s["n_user_prompts"] for s in sessions),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "projects": len(by_project),
            "match_sources": dict(src_counts),
            "last_ts": max((s["last_ts"] or "" for s in sessions), default=""),
        },
        "pace": {"last7": last7, "prev7": prev7},
        "daily": daily,
        "punch": punch,
        "heatmap": {"days": days, "rows": heat_rows},
        "projects": projects,
        "tokens_by_project": token_rows,
        "loc": loc_data,
        "unmatched": unmatched_rows,
    }


LOC_KINDS = ("code", "docs", "other")


def _build_loc(sessions, tdays):
    """書いた行数（parser の loc/days_loc/files_loc を集計）。

    ・日別（直近 TREND_DAYS 日）= code/docs/other の積み上げ＋removed
    ・案件別（全期間累計）= code/docs/other・removed・未計測件数
    ・ファイル別（直近 TREND_DAYS 日の上位）
    unmeasured（sed -i／python直書き等の行数が取れない編集コマンド）は件数だけ添える＝正直な脚注。
    """
    day_set = set(tdays)
    by_day = {d: {"code": 0, "docs": 0, "other": 0, "removed": 0} for d in tdays}
    by_proj = defaultdict(lambda: {"code": 0, "docs": 0, "other": 0, "removed": 0,
                                   "unmeasured": 0, "n_sessions": 0})
    files = defaultdict(int)
    tot_all = {"added": 0, "removed": 0, "code": 0, "docs": 0, "other": 0, "unmeasured": 0,
               "bash_added": 0, "n_edits": 0}
    for s in sessions:
        loc = s.get("loc") or {}
        if not loc:
            continue
        for k in tot_all:
            tot_all[k] += loc.get(k, 0)
        p = s["project"] or UNCLASSIFIED
        r = by_proj[p]
        for k in ("code", "docs", "other", "removed", "unmeasured"):
            r[k] += loc.get(k, 0)
        if loc.get("added") or loc.get("unmeasured"):
            r["n_sessions"] += 1
        in_window = False
        for d, v in (s.get("days_loc") or {}).items():
            if d in day_set:
                in_window = True
                for k in ("code", "docs", "other", "removed"):
                    by_day[d][k] += v.get(k, 0)
        if in_window:
            for name, n in (s.get("files_loc") or {}).items():
                files[name] += n
    added_day = [sum(by_day[d][k] for k in LOC_KINDS) for d in tdays]
    win = {k: sum(by_day[d][k] for d in tdays) for k in LOC_KINDS + ("removed",)}
    win["added"] = sum(win[k] for k in LOC_KINDS)
    proj_rows = [{"project": p, "added": sum(v[k] for k in LOC_KINDS), **v}
                 for p, v in by_proj.items()]
    proj_rows.sort(key=lambda r: -r["added"])
    return {
        "days": tdays,
        "daily": {k: [by_day[d][k] for d in tdays] for k in LOC_KINDS + ("removed",)},
        "window": win,
        "pace": {"last7": sum(added_day[-7:]), "prev7": sum(added_day[-14:-7])},
        "all_time": tot_all,
        "by_project": proj_rows,
        "top_files": sorted(files.items(), key=lambda kv: -kv[1])[:12],
    }


# ---- HTML（自己完結1枚・外部リソース参照ゼロ・色は全て CSS 変数） ----

CSS = """
*{box-sizing:border-box}
:root{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10); --wash:rgba(255,255,255,.055);
  --cat-1:#3987e5; --cat-2:#d95926; --cat-3:#199e70;
  --seq-0:#232321; --seq-1:#0d366b; --seq-2:#184f95; --seq-3:#256abf;
  --seq-4:#3987e5; --seq-5:#6da7ec; --seq-6:#cde2fb;
}
:root[data-theme="light"]{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10); --wash:rgba(11,11,11,.045);
  --cat-1:#2a78d6; --cat-2:#eb6834; --cat-3:#1baf7a;
  --seq-0:#f0efec; --seq-1:#cde2fb; --seq-2:#9ec5f4; --seq-3:#6da7ec;
  --seq-4:#3987e5; --seq-5:#256abf; --seq-6:#184f95;
}
body{margin:0;padding:26px 22px 56px;background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;
  font-size:13px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto}
a{color:var(--cat-1)}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px}
h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.01em}
h1 .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--cat-1);
  margin-right:9px;vertical-align:2px}
.sub{color:var(--muted);font-size:11.5px;margin-top:3px}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;
  padding:5px 13px;font-size:11.5px;cursor:pointer;font-family:inherit;white-space:nowrap}
.tgl:hover{background:var(--wash);color:var(--ink)}
.tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
a.tgl{text-decoration:none;display:inline-block}
.nav{display:flex;gap:8px;align-items:center}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:17px 19px 15px;margin:12px 0}
.card h2{font-size:12.5px;font-weight:600;margin:0 0 2px;letter-spacing:.02em}
.card .cap{color:var(--muted);font-size:11px;margin:0 0 14px}
.note{color:var(--muted);font-size:11px;margin:9px 0 0}
/* ── ヒーロー＋statタイル ───────────────────────────── */
.hero-grid{display:grid;grid-template-columns:minmax(260px,1.5fr) repeat(4,minmax(110px,1fr));
  gap:12px;margin:12px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:15px 17px}
.tile .l{color:var(--muted);font-size:11px;letter-spacing:.03em}
.tile .v{font-size:26px;font-weight:600;line-height:1.2;margin-top:2px;color:var(--ink)}
.tile.hero .v{font-size:48px;letter-spacing:-.02em;line-height:1.05;margin-top:4px}
.tile .d{color:var(--ink2);font-size:11.5px;margin-top:4px}
.tile .d b{font-weight:600;font-variant-numeric:tabular-nums}
.spark{display:block;margin-top:8px;width:100%;height:34px;overflow:visible}
/* ── SVG 共通 ─────────────────────────────────────── */
svg{font-family:inherit}
svg text{fill:var(--muted);font-size:10.5px}
svg text.lbl{fill:var(--ink2);font-size:11px;font-weight:600}
.gl{stroke:var(--grid);stroke-width:1}
.ax{stroke:var(--axis);stroke-width:1}
.ln{fill:none;stroke:var(--cat-1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ar{fill:var(--cat-1);fill-opacity:.10}
.pt{fill:var(--cat-1);stroke:var(--surface);stroke-width:2}
.hit{fill:transparent}
.hit:hover{fill:var(--wash)}
/* ── パンチカード／ヒートマップ ─────────────────────── */
.punch{display:grid;grid-template-columns:26px repeat(24,1fr);gap:2px;align-items:center}
.punch .h{font-size:9px;color:var(--muted);text-align:center;line-height:1.1}
.punch .r{font-size:10.5px;color:var(--ink2);text-align:right;padding-right:4px}
.punch .c{height:16px;border-radius:3px}
.heat{display:grid;gap:2px;align-items:center}
.heat .h{font-size:9.5px;color:var(--muted);text-align:center}
.heat .h.wk{color:var(--ink2)}
.heat .p{font-size:11.5px;color:var(--ink2);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;padding-right:6px}
.heat .c{height:19px;border-radius:3px}
.heat .t{font-size:10.5px;color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}
/* ランプ段は単独クラス（.punch .c 等より詳細度を下げない＝base側に background を持たせない） */
.s0{background:var(--seq-0)}.s1{background:var(--seq-1)}.s2{background:var(--seq-2)}
.s3{background:var(--seq-3)}.s4{background:var(--seq-4)}.s5{background:var(--seq-5)}
.s6{background:var(--seq-6)}
.scale{display:flex;align-items:center;gap:5px;color:var(--muted);font-size:10.5px;margin-top:11px}
.scale i{width:20px;height:10px;border-radius:2px;display:inline-block}
/* ── 横バー ───────────────────────────────────────── */
.bar{display:grid;grid-template-columns:minmax(90px,168px) 1fr 124px;gap:12px;
  align-items:center;margin:7px 0}
.bar .n{font-size:12px;color:var(--ink2);text-align:right;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.bar .t{height:14px;background:var(--wash);border-radius:3px;overflow:hidden}
.bar .f{height:14px;background:var(--cat-1);border-radius:0 4px 4px 0;min-width:2px}
.bar .v{font-size:11.5px;color:var(--ink2);font-variant-numeric:tabular-nums}
.bar .v s{color:var(--muted);text-decoration:none;font-size:10.5px}
/* 横バーの積み上げ版（書いた行数: code/docs/other）。塗りの間は 2px の面色で切る */
.bar .t.st{display:flex;gap:2px;background:none}
.bar .t.st i{display:block;height:14px;min-width:2px;border-radius:2px}
.bar .t.st i:first-child{border-radius:3px 2px 2px 3px}
.bar .t.st i:last-child{border-radius:2px 4px 4px 2px}
.c1{fill:var(--cat-1);background:var(--cat-1)}.c3{fill:var(--cat-3);background:var(--cat-3)}
.c0{fill:var(--wash);background:var(--wash)}
.stat-row{display:flex;flex-wrap:wrap;gap:6px 22px;margin:0 0 12px;color:var(--ink2);font-size:12px}
.stat-row b{font-size:20px;font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums;
  margin-right:4px}
.stat-row .l{color:var(--muted);font-size:11px}
/* ── 積み上げ（マッチ根拠） ────────────────────────── */
.stack{display:flex;gap:2px;height:22px;margin:2px 0 12px}
.stack>i{display:block;border-radius:2px}
.stack>i:first-child{border-radius:4px 2px 2px 4px}
.stack>i:last-child{border-radius:2px 4px 4px 2px}
.k1{background:var(--cat-1)}.k2{background:var(--cat-2)}.k3{background:var(--cat-3)}
.lg{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:11.5px;color:var(--ink2)}
.lg span{display:flex;align-items:center;gap:6px}
.lg i{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.lg b{font-weight:600;font-variant-numeric:tabular-nums}
/* ── 案件×セッション地図 ──────────────────────────── */
details.proj{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:11px 19px;margin:8px 0}
details.proj summary{cursor:pointer;font-size:13px;font-weight:600;list-style:none;
  display:flex;flex-wrap:wrap;align-items:baseline;gap:10px}
details.proj summary::-webkit-details-marker{display:none}
details.proj summary::before{content:"▸";color:var(--muted);font-weight:400;margin-right:2px}
details.proj[open] summary::before{content:"▾"}
details.proj summary .meta{color:var(--muted);font-size:11px;font-weight:400}
details.proj summary .pill{font-size:10.5px;font-weight:600;color:var(--ink2);
  background:var(--wash);border-radius:999px;padding:1px 9px}
/* 台帳ページ（エージェントカウンター）へ。生成済みの案件だけ出る */
details.proj summary .lgl{text-decoration:none;font-size:11px;opacity:.5;margin-left:-4px}
details.proj summary .lgl:hover{opacity:1}
table{border-collapse:collapse;font-size:12px;width:100%;margin:10px 0 4px}
th{text-align:left;color:var(--muted);font-weight:400;font-size:10.5px;letter-spacing:.03em;
  padding:3px 12px 5px 0;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:5px 12px 5px 0;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
td.txt{font-variant-numeric:normal}
tr:last-child td{border-bottom:none}
.xf{color:var(--cat-2);font-size:10.5px}
.tv{margin-top:12px;border-top:1px solid var(--border);padding-top:8px}
.tv summary{cursor:pointer;color:var(--muted);font-size:11px;list-style:none}
.tv summary::-webkit-details-marker{display:none}
.tv summary::before{content:"⊞ ";color:var(--muted)}
.tv[open] summary::before{content:"⊟ "}
.tv .sc{max-height:280px;overflow:auto}
h2.sec{font-size:13px;font-weight:600;margin:26px 0 2px}
footer{margin-top:26px;color:var(--muted);font-size:11px;line-height:1.8}
code{background:var(--wash);border-radius:4px;padding:1px 5px;font-size:11px}
#tip{position:fixed;z-index:9;pointer-events:none;opacity:0;transition:opacity .1s;
  background:var(--surface);color:var(--ink);border:1px solid var(--border);border-radius:8px;
  padding:6px 10px;font-size:11.5px;white-space:nowrap;
  box-shadow:0 6px 22px rgba(0,0,0,.35)}
@media (max-width:820px){
  .hero-grid{grid-template-columns:repeat(2,1fr)}
  .bar{grid-template-columns:minmax(70px,110px) 1fr 96px}
}
""" + MDL_CSS

JS = """
(function(){
  var root=document.documentElement, btn=document.getElementById('tgl');
  try{var s=localStorage.getItem('csv-theme'); if(s) root.dataset.theme=s;}catch(e){}
  function lab(){btn.textContent = root.dataset.theme==='light' ? '☾ ダーク' : '☀ ライト';}
  lab();
  btn.onclick=function(){
    root.dataset.theme = root.dataset.theme==='light' ? 'dark' : 'light';
    try{localStorage.setItem('csv-theme', root.dataset.theme);}catch(e){}
    lab();
  };
  var tip=document.getElementById('tip');
  document.addEventListener('mouseover',function(e){
    var t=e.target.closest('[data-tip]'); if(!t) return;
    tip.textContent=t.getAttribute('data-tip'); tip.style.opacity='1';
  });
  document.addEventListener('mousemove',function(e){
    if(tip.style.opacity!=='1') return;
    var x=e.clientX+14, y=e.clientY+16, w=tip.offsetWidth, h=tip.offsetHeight;
    if(x+w>innerWidth-8) x=e.clientX-w-14;
    if(y+h>innerHeight-8) y=e.clientY-h-14;
    tip.style.left=x+'px'; tip.style.top=y+'px';
  });
  document.addEventListener('mouseout',function(e){
    if(e.target.closest('[data-tip]')) tip.style.opacity='0';
  });
})();
"""


def _tile(label, value, detail="", cls=""):
    d = f'<div class="d">{detail}</div>' if detail else ""
    return (f'<div class="tile {cls}"><div class="l">{esc(label)}</div>'
            f'<div class="v">{value}</div>{d}</div>')


def _spark(values):
    """ヒーロー数字に添える12点スパークライン（軸なし・直近の1点だけ強調）。"""
    if not values:
        return ""
    w, h, pad = 240.0, 34.0, 4.0
    peak = max(values) or 1
    n = len(values)
    step = (w - pad * 2) / max(1, n - 1)
    pts = [(pad + i * step, pad + (h - pad * 2) * (1 - v / peak)) for i, v in enumerate(values)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{line} L{pts[-1][0]:.1f},{h - pad:.1f} L{pts[0][0]:.1f},{h - pad:.1f} Z"
    lx, ly = pts[-1]
    return (f'<svg class="spark" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
            f'aria-hidden="true"><path class="ar" d="{area}"/><path class="ln" d="{line}"/>'
            f'<circle class="pt" cx="{lx:.1f}" cy="{ly:.1f}" r="3.2"/></svg>')


def _trend_svg(daily):
    """日別の出力トークン推移（面＋線・ホバーで日別の値）。"""
    days, vals = daily["days"], daily["tokens"]
    msgs = daily["assistant"]
    n = len(days)
    W, H, L, R, T, B = 1100.0, 230.0, 54.0, 16.0, 14.0, 26.0
    pw, ph = W - L - R, H - T - B
    base = T + ph
    peak = max(vals) or 1
    # y軸上限は切りのいい値へ。ピークの15%上まで取る＝最大値の直接ラベルが軸ラベルと衝突しない
    want = peak * 1.15
    unit = next((u for u in (500, 1000, 2000, 5000, 10000, 20000, 50000,
                             100000, 200000, 500000, 1000000) if want / u <= 6), 2000000)
    top = -(-want // unit) * unit
    step = pw / max(1, n - 1)

    def x(i):
        return L + i * step

    def y(v):
        return T + ph * (1 - v / top)

    grid, ylab = [], []
    for f in (0, 0.5, 1):
        gy = T + ph * (1 - f)
        cls = "ax" if f == 0 else "gl"
        grid.append(f'<line class="{cls}" x1="{L}" y1="{gy:.1f}" x2="{W - R}" y2="{gy:.1f}"/>')
        ylab.append(f'<text x="{L - 8}" y="{gy + 3.5:.1f}" text-anchor="end">{_k(int(top * f))}</text>')
    pts = [(x(i), y(v)) for i, v in enumerate(vals)]
    line = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    area = f"{line} L{pts[-1][0]:.1f},{base:.1f} L{pts[0][0]:.1f},{base:.1f} Z"

    xlab = []
    for i, d in enumerate(days):
        if i % 5 == 0 or i == n - 1:
            xlab.append(f'<text x="{x(i):.1f}" y="{H - 8:.1f}" text-anchor="middle">'
                        f'{int(d[5:7])}/{int(d[8:10])}</text>')
    hits = []
    for i, d in enumerate(days):
        wd = WD_LABELS[date.fromisoformat(d).weekday()]
        tip = f'{d[5:].replace("-", "/")}（{wd}） 出力 {_k(vals[i])} ／ 応答 {msgs[i]}'
        hits.append(f'<rect class="hit" x="{x(i) - step / 2:.1f}" y="{T}" width="{step:.1f}" '
                    f'height="{ph:.1f}" data-tip="{esc(tip)}"/>')
    # 直接ラベルは「最大の日」と「最終日」だけ（全点に数字は置かない）
    marks = []
    if max(vals) > 0:
        imax = vals.index(max(vals))
        anchor = "end" if imax > n * 0.9 else "start" if imax < n * 0.1 else "middle"
        marks += [f'<circle class="pt" cx="{x(imax):.1f}" cy="{y(vals[imax]):.1f}" r="4"/>',
                  f'<text class="lbl" x="{x(imax):.1f}" y="{y(vals[imax]) - 10:.1f}" '
                  f'text-anchor="{anchor}">{_k(vals[imax])}</text>']
        if imax != n - 1:
            marks.append(f'<circle class="pt" cx="{x(n - 1):.1f}" cy="{y(vals[-1]):.1f}" r="4"/>')
    return (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" style="width:100%;height:auto" '
            f'role="img" aria-label="日別の出力トークン推移">'
            f'{"".join(grid)}<path class="ar" d="{area}"/><path class="ln" d="{line}"/>'
            f'{"".join(marks)}{"".join(ylab)}{"".join(xlab)}{"".join(hits)}</svg>')


def _punch_html(punch):
    """曜日×時間帯の稼働リズム（7×24・単一色相6段）。"""
    peak = max((c for row in punch for c in row), default=0)
    cells = ['<div class="h"></div>']
    for h in range(24):
        cells.append(f'<div class="h">{h if h % 3 == 0 else ""}</div>')
    for wd in range(7):
        cells.append(f'<div class="r">{WD_LABELS[wd]}</div>')
        for h in range(24):
            v = punch[wd][h]
            b = _bin(v, peak)
            tip = f"{WD_LABELS[wd]}曜 {h:02d}時台 ／ 応答 {v:,}"
            cells.append(f'<div class="c s{b}" data-tip="{esc(tip)}"></div>')
    scale = ('<div class="scale">少<i class="s1"></i><i class="s2"></i><i class="s3"></i>'
             '<i class="s4"></i><i class="s5"></i><i class="s6"></i>多'
             f'<span style="margin-left:8px">最大 {peak:,}／時</span></div>')
    # 表ビュー: 曜日ごとの合計と最も濃い時間帯
    rows = []
    for wd in range(7):
        tot = sum(punch[wd])
        top_h = max(range(24), key=lambda h: punch[wd][h]) if tot else 0
        rows.append(f'<tr><td class="txt">{WD_LABELS[wd]}</td><td>{tot:,}</td>'
                    f'<td>{top_h:02d}時台（{punch[wd][top_h]:,}）</td></tr>')
    table = ('<details class="tv"><summary>数値で見る（曜日別）</summary>'
             '<table><tr><th>曜日</th><th>応答</th><th>最も濃い時間帯</th></tr>'
             f'{"".join(rows)}</table></details>')
    return f'<div class="punch">{"".join(cells)}</div>{scale}{table}'


def _heat_html(heat):
    days, rows = heat["days"], heat["rows"][:HEATMAP_MAX_ROWS]
    peak = max((c for r in rows for c in r["counts"]), default=0)
    cols = f"minmax(90px,190px) repeat({len(days)},1fr) 42px"
    cells = ['<div class="p"></div>']
    for d in days:
        wd = date.fromisoformat(d).weekday()
        cls = "h wk" if wd >= 5 else "h"
        cells.append(f'<div class="{cls}">{int(d[5:7])}/{int(d[8:10])}</div>')
    cells.append('<div class="t">計</div>')
    for r in rows:
        cells.append(f'<div class="p" data-tip="{esc(r["project"])}">{esc(r["project"])}</div>')
        for d, c in zip(days, r["counts"]):
            b = _bin(c, peak)
            tip = f'{r["project"]} {d[5:].replace("-", "/")} ／ 応答 {c}'
            cells.append(f'<div class="c s{b}" data-tip="{esc(tip)}"></div>')
        cells.append(f'<div class="t">{r["total"]}</div>')
    scale = ('<div class="scale">少<i class="s1"></i><i class="s2"></i><i class="s3"></i>'
             '<i class="s4"></i><i class="s5"></i><i class="s6"></i>多'
             f'<span style="margin-left:8px">最大 {peak:,}／日</span></div>')
    dropped = len(heat["rows"]) - len(rows)
    note = f'<p class="note">※ 上位{HEATMAP_MAX_ROWS}案件を表示（{dropped}案件省略）</p>' if dropped > 0 else ""
    trows = "".join(f'<tr><td class="txt">{esc(r["project"])}</td><td>{r["total"]}</td>'
                    f'<td>{max(r["counts"])}</td></tr>' for r in heat["rows"])
    table = ('<details class="tv"><summary>数値で見る（案件別・14日計）</summary><div class="sc">'
             '<table><tr><th>案件</th><th>応答計</th><th>最大/日</th></tr>'
             f'{trows}</table></div></details>')
    return (f'<div class="heat" style="grid-template-columns:{cols}">{"".join(cells)}</div>'
            f'{scale}{note}{table}')


def _loc_svg(loc):
    """日別の書いた行数（code/docs/other の積み上げ棒・塗りの間は 2px の面色）。"""
    days, daily = loc["days"], loc["daily"]
    n = len(days)
    tot = [daily["code"][i] + daily["docs"][i] + daily["other"][i] for i in range(n)]
    W, H, L, R, T, B = 1100.0, 230.0, 54.0, 16.0, 14.0, 26.0
    pw, ph = W - L - R, H - T - B
    base = T + ph
    peak = max(tot) or 1
    want = peak * 1.15
    unit = next((u for u in (100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000)
                 if want / u <= 6), 100000)
    top = -(-want // unit) * unit
    slot = pw / max(1, n)
    bw = max(2.0, slot - 4)

    def x(i):
        return L + i * slot + (slot - bw) / 2

    def y(v):
        return T + ph * (1 - v / top)

    grid, ylab = [], []
    for f in (0, 0.5, 1):
        gy = T + ph * (1 - f)
        cls = "ax" if f == 0 else "gl"
        grid.append(f'<line class="{cls}" x1="{L}" y1="{gy:.1f}" x2="{W - R}" y2="{gy:.1f}"/>')
        ylab.append(f'<text x="{L - 8}" y="{gy + 3.5:.1f}" text-anchor="end">{_k(int(top * f))}</text>')
    rects, hits, xlab = [], [], []
    for i, d in enumerate(days):
        acc = 0.0
        for kind, cls in (("code", "c1"), ("docs", "c3"), ("other", "c0")):
            v = daily[kind][i]
            if v <= 0:
                continue
            y1, y0 = y(acc), y(acc + v)
            h = max(0.0, y1 - y0 - (2 if acc > 0 else 0))  # 上に積む塗りとの間を 2px 空ける
            rects.append(f'<rect class="{cls}" x="{x(i):.1f}" y="{y0:.1f}" width="{bw:.1f}" '
                         f'height="{h:.1f}" rx="1.5"/>')
            acc += v
        wd = WD_LABELS[date.fromisoformat(d).weekday()]
        tip = (f'{d[5:].replace("-", "/")}（{wd}） +{tot[i]:,}行 ＝ コード {daily["code"][i]:,} ／ '
               f'ドキュメント {daily["docs"][i]:,} ／ その他 {daily["other"][i]:,}'
               f'　（削除 {daily["removed"][i]:,}）')
        hits.append(f'<rect class="hit" x="{L + i * slot:.1f}" y="{T}" width="{slot:.1f}" '
                    f'height="{ph:.1f}" data-tip="{esc(tip)}"/>')
        if i % 5 == 0 or i == n - 1:
            xlab.append(f'<text x="{x(i) + bw / 2:.1f}" y="{H - 8:.1f}" text-anchor="middle">'
                        f'{int(d[5:7])}/{int(d[8:10])}</text>')
    marks = []
    if peak > 0:
        imax = tot.index(peak)
        anchor = "end" if imax > n * 0.9 else "start" if imax < n * 0.1 else "middle"
        marks.append(f'<text class="lbl" x="{x(imax) + bw / 2:.1f}" y="{y(peak) - 6:.1f}" '
                     f'text-anchor="{anchor}">{tot[imax]:,}</text>')
        if imax != n - 1 and tot[-1] > 0:
            marks.append(f'<text class="lbl" x="{x(n - 1) + bw / 2:.1f}" y="{y(tot[-1]) - 6:.1f}" '
                         f'text-anchor="end">{tot[-1]:,}</text>')
    return (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" style="width:100%;height:auto" '
            f'role="img" aria-label="日別の書いた行数（コード／ドキュメント／その他）">'
            f'{"".join(grid)}{"".join(rects)}{"".join(marks)}{"".join(ylab)}{"".join(xlab)}'
            f'{"".join(hits)}</svg>')


LOC_LEGEND = ('<div class="lg"><span><i class="c1"></i>コード</span>'
              '<span><i class="c3"></i>ドキュメント</span>'
              '<span><i class="c0" style="border:1px solid var(--border)"></i>その他</span></div>')


def _loc_card_html(loc):
    """✍️ 書いた行数（直近30日）カード。ヒーロー数字＋積み上げ棒＋凡例＋表。"""
    w, pace = loc["window"], loc["pace"]
    at = loc["all_time"]
    share = (w["code"] / w["added"] * 100) if w["added"] else 0
    if pace["prev7"]:
        pct = (pace["last7"] - pace["prev7"]) / pace["prev7"] * 100
        delta = f'（前週比 {"+" if pct >= 0 else "−"}{abs(pct):.0f}%）'
    else:
        delta = ""
    stats = (f'<div class="stat-row">'
             f'<span><b>{w["added"]:,}</b>行 <span class="l">直近{TREND_DAYS}日</span></span>'
             f'<span><b>{share:.0f}%</b><span class="l">がコード（{w["code"]:,}行・ドキュメント {w["docs"]:,}行）</span></span>'
             f'<span><b>{pace["last7"]:,}</b><span class="l">直近7日 {delta}</span></span>'
             f'<span><b>{w["removed"]:,}</b><span class="l">行を削除</span></span>'
             f'<span><b>{at["added"]:,}</b><span class="l">行 全期間累計（未計測の編集 {at["unmeasured"]:,}件）</span></span>'
             f'</div>')
    days, d = loc["days"], loc["daily"]
    trows = "".join(
        f'<tr><td class="txt">{days[i][5:].replace("-", "/")}</td>'
        f'<td>{d["code"][i] + d["docs"][i] + d["other"][i]:,}</td><td>{d["code"][i]:,}</td>'
        f'<td>{d["docs"][i]:,}</td><td>{d["other"][i]:,}</td><td>{d["removed"][i]:,}</td></tr>'
        for i in range(len(days) - 1, -1, -1)
        if d["code"][i] + d["docs"][i] + d["other"][i] + d["removed"][i])
    frows = "".join(f'<tr><td class="txt">{esc(name)}</td><td>{n:,}</td></tr>'
                    for name, n in loc["top_files"])
    return (f'{stats}{_loc_svg(loc)}<div style="margin-top:10px">{LOC_LEGEND}</div>'
            f'<p class="note">Edit/Write/MultiEdit/NotebookEdit と Bash/PowerShell のファイル直書き'
            f'（ヒアドキュメント）の<b>成功分だけ</b>。sed -i や python 直書きは行数が取れないので件数のみ。'
            f'サブエージェントの編集も含む。Anthropic 管理コンソールの「コード行数」とは別物＝社内比較用の自前指標</p>'
            '<details class="tv"><summary>数値で見る</summary><div class="sc">'
            f'<table><tr><th>日</th><th>+行</th><th>コード</th><th>ドキュメント</th><th>その他</th>'
            f'<th>削除</th></tr>{trows}</table>'
            f'<table><tr><th>ファイル（直近{TREND_DAYS}日・上位）</th><th>+行</th></tr>{frows}</table>'
            '</div></details>')


def _loc_bars_html(rows):
    """案件別の書いた行数（全期間累計・code/docs/other の横積み上げ）。"""
    top = rows[:TOKEN_BAR_ROWS]
    peak = max((r["added"] for r in top), default=1) or 1
    total = sum(r["added"] for r in rows) or 1
    out = []
    for r in top:
        segs = []
        for kind, cls in (("code", "c1"), ("docs", "c3"), ("other", "c0")):
            v = r[kind]
            if v > 0:
                segs.append(f'<i class="{cls}" style="width:{v / peak * 100:.2f}%"></i>')
        share = r["added"] / total * 100
        tip = (f'{r["project"]} ／ +{r["added"]:,}行 ＝ コード {r["code"]:,} ／ ドキュメント {r["docs"]:,} ／ '
               f'その他 {r["other"]:,} ／ 削除 {r["removed"]:,} ／ 未計測 {r["unmeasured"]}件 ／ '
               f'{r["n_sessions"]}セッション')
        out.append(
            f'<div class="bar" data-tip="{esc(tip)}"><div class="n">{esc(r["project"])}</div>'
            f'<div class="t st">{"".join(segs)}</div>'
            f'<div class="v">{r["added"]:,} <s>{share:.0f}%・{r["n_sessions"]}本</s></div></div>')
    dropped = len(rows) - len(top)
    if dropped > 0:
        rest = sum(r["added"] for r in rows[TOKEN_BAR_ROWS:])
        out.append(f'<p class="note">※ ほか{dropped}案件・計 {rest:,}行</p>')
    trows = "".join(
        f'<tr><td class="txt">{esc(r["project"])}</td><td>{r["added"]:,}</td><td>{r["code"]:,}</td>'
        f'<td>{r["docs"]:,}</td><td>{r["other"]:,}</td><td>{r["removed"]:,}</td>'
        f'<td>{r["unmeasured"]}</td><td>{r["n_sessions"]}</td></tr>' for r in rows)
    return ("".join(out) + f'<div style="margin-top:10px">{LOC_LEGEND}</div>'
            '<details class="tv"><summary>数値で見る</summary><div class="sc">'
            '<table><tr><th>案件</th><th>+行</th><th>コード</th><th>ドキュメント</th><th>その他</th>'
            f'<th>削除</th><th>未計測</th><th>セッション</th></tr>{trows}</table></div></details>')


def _bars_html(token_rows):
    rows = token_rows[:TOKEN_BAR_ROWS]
    peak = max((r["output_tokens"] for r in rows), default=1) or 1
    total = sum(r["output_tokens"] for r in token_rows) or 1
    out = []
    for r in rows:
        w = max(0.4, r["output_tokens"] / peak * 100)
        share = r["output_tokens"] / total * 100
        tip = (f'{r["project"]} ／ 出力 {r["output_tokens"]:,} tok ／ 全体の {share:.1f}% ／ '
               f'{r["n_sessions"]}セッション')
        out.append(
            f'<div class="bar" data-tip="{esc(tip)}"><div class="n">{esc(r["project"])}</div>'
            f'<div class="t"><div class="f" style="width:{w:.1f}%"></div></div>'
            f'<div class="v">{_k(r["output_tokens"])} <s>{share:.0f}%・{r["n_sessions"]}本</s></div></div>')
    dropped = len(token_rows) - len(rows)
    if dropped > 0:
        rest = sum(r["output_tokens"] for r in token_rows[TOKEN_BAR_ROWS:])
        out.append(f'<p class="note">※ ほか{dropped}案件・計 {_k(rest)}</p>')
    return "".join(out)


def _sources_html(src_counts, matched, unmatched):
    """マッチ根拠の内訳。「採番100%」の中身（採番/キーワード/WS落ち）を正直に出す。"""
    kick = sum(v for k, v in src_counts.items() if k in ("ai-title", "first-prompts", "last-prompt"))
    hint = src_counts.get("title-hint", 0)
    ws = src_counts.get("workspace", 0)
    total = matched + unmatched or 1
    seg = [("k1", "採番合言葉", kick), ("k2", "キーワード", hint), ("k3", "ワークスペース", ws)]
    if unmatched:
        seg.append(("", "未分類", unmatched))
    bars = "".join(
        f'<i class="{c}" style="width:{n / total * 100:.2f}%;{"background:var(--wash)" if not c else ""}" '
        f'data-tip="{esc(f"{lab}: {n}本（{n / total * 100:.0f}%）")}"></i>'
        for c, lab, n in seg if n)
    lg = "".join(
        f'<span><i class="{c}" style="{"background:var(--wash)" if not c else ""}"></i>'
        f'{esc(lab)} <b>{n}</b>本</span>' for c, lab, n in seg)
    trows = "".join(f'<tr><td class="txt">{esc(lab)}</td><td>{n}</td>'
                    f'<td>{n / total * 100:.1f}%</td></tr>' for _, lab, n in seg)
    return (f'<div class="stack">{bars}</div><div class="lg">{lg}</div>'
            f'<p class="note">「採番合言葉」で始めたセッションだけが地図に S番号付きで並ぶ。'
            f'ワークスペース落ちは案件が1つに潰れている＝地図の解像度が上がる余地。</p>'
            '<details class="tv"><summary>数値で見る</summary>'
            f'<table><tr><th>根拠</th><th>本数</th><th>比率</th></tr>{trows}</table></details>')


def _ledger_link(project):
    """案件名の隣に台帳ページ（counter_gen.py 生成）への 🎫 を添える。無ければ何も出さない。

    summary 内なので stopPropagation を付ける（クリックで details が開閉しないように）。
    """
    name = f"{slug(project, 'p-')}.html" if project else ""
    if not name or not (REPO_ROOT / "reports" / "counter" / name).is_file():
        return ""
    return (f'<a class="lgl" href="counter/{name}" title="台帳を見る"'
            f' onclick="event.stopPropagation()">🎫</a>')


def _projects_html(projects):
    recent_limit = (datetime.now().astimezone() - timedelta(days=RECENT_OPEN_DAYS)).isoformat()
    out = []
    for p in projects:
        is_open = " open" if (p["last_ts"] or "") >= recent_limit else ""
        rows = []
        for b in p["bundles"]:
            sn = f"S{b['session_no']}" if b["session_no"] is not None else "—"
            if b["n_files"] > 1:
                sn += f' <span class="xf">×{b["n_files"]}f</span>'
            mdl = " ".join(model_badge(m) for m in b.get("models", [])) or "·"
            loc_tip = (f'コード {b["loc_code"]:,} ／ ドキュメント {b["loc_docs"]:,} ／ '
                       f'削除 {b["loc_removed"]:,}')
            rows.append(
                f'<tr><td>{sn}</td><td class="txt">{mdl}</td>'
                f'<td class="txt">{esc(_span(b["first_ts"], b["last_ts"]))}</td>'
                f'<td>{b["n_user_prompts"]}</td><td>{b["n_assistant"]}</td>'
                f'<td>{b["n_tool_use"]}</td><td>{b["n_subagents"] or "·"}</td>'
                f'<td>{b["output_tokens"] / 1000:,.0f}</td>'
                f'<td data-tip="{esc(loc_tip)}">{b["loc_added"]:,}</td>'
                f'<td class="txt">{esc("、".join(b["workspaces"]))}</td></tr>')
        out.append(
            f'<details class="proj"{is_open}><summary><b>{esc(p["name"])}</b>'
            f'{_ledger_link(p["name"])}'
            f'<span class="pill">{p["n_bundles"]}セッション</span>'
            f'{model_badge(p.get("model"), note="直近セッションのモデル")}'
            f'<span class="meta">{p["n_files"]}ファイル・最終 {_fmt(p["last_ts"])}'
            f'・出力 {_k(p["output_tokens"])}・+{p["loc_added"]:,}行</span></summary>'
            '<table><tr><th>S</th><th>model</th><th>期間</th><th>発話</th><th>応答</th><th>ツール</th>'
            f'<th>sub-ag</th><th>出力tok(k)</th><th>+行</th><th>WS</th></tr>{"".join(rows)}</table></details>')
    return "".join(out)


def _unmatched_html(unmatched):
    if not unmatched:
        return ""
    rows = "".join(
        f'<tr><td class="txt">{_fmt(u["last_ts"])}</td>'
        f'<td class="txt">{esc(u["ai_title"] or "（無題）")}</td>'
        f'<td>{u["n_user_prompts"]}</td><td>{u["size_kb"]:,}</td>'
        f'<td class="txt">{esc(u["workspace"])}</td></tr>' for u in unmatched)
    return (f'<details class="proj"><summary><b>❓ 未分類セッション</b>'
            f'<span class="pill">{len(unmatched)}本</span>'
            '<span class="meta">識別は ai-title のみ・本文スニペットは l0_report.py で</span></summary>'
            '<table><tr><th>最終稼働</th><th>タイトル</th><th>発話</th><th>KB</th><th>WS</th></tr>'
            f'{rows}</table></details>')


def render_html(data):
    t = data["totals"]
    gen = _fmt(data["generated_at"], "%Y-%m-%d %H:%M")
    daily = data["daily"]
    pace, prev = data["pace"]["last7"], data["pace"]["prev7"]
    if prev:
        pct = (pace - prev) / prev * 100
        delta = f'直近7日 <b>{_k(pace)}</b>（前週比 {"+" if pct >= 0 else "−"}{abs(pct):.0f}%）'
    else:
        delta = f"直近7日 <b>{_k(pace)}</b>"
    tools_per = t["tool_use"] / t["user_prompts"] if t["user_prompts"] else 0
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>アクティビティ — {gen}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1><span class="dot"></span>アクティビティ</h1>
    <div class="sub">生成 {gen} ／ ~/.claude/projects（{t["size_mb"]:,.0f} MB・最終稼働 {_fmt(t["last_ts"])}）</div>
  </div>
  <div class="nav">{nav_html("map")}<button class="tgl" id="tgl" type="button">☀ ライト</button></div>
</header>

<div class="hero-grid">
  <div class="tile hero">
    <div class="l">出力トークン累計</div>
    <div class="v">{_k(t["output_tokens"])}</div>
    <div class="d">{delta}</div>
    {_spark(daily["tokens"][-SPARK_DAYS:])}
  </div>
  {_tile("セッション", f'{t["sessions"]:,}', f'{t["projects"]} 案件')}
  {_tile("発話", f'{t["user_prompts"]:,}', f'Claude の応答 {t["assistant"]:,}')}
  {_tile("ツール呼び出し", f'{t["tool_use"]:,}', f'発話あたり {tools_per:.0f} 回')}
  {_tile("サブエージェント", f'{t["subagents"]:,}', "delegate した回数")}
</div>

<div class="card">
  <h2>📈 日別の出力トークン（直近{TREND_DAYS}日）</h2>
  <p class="cap">面＝出力トークン。ホバーでその日の応答数も出る</p>
  {_trend_svg(daily)}
</div>

<div class="card">
  <h2>✍️ 書いた行数（直近{TREND_DAYS}日）</h2>
  <p class="cap">棒＝その日に Claude が書いた行数。コード／ドキュメント（md・txt・csv 等）／その他の積み上げ</p>
  {_loc_card_html(data["loc"])}
</div>

<div class="card">
  <h2>🕐 稼働リズム（曜日 × 時間帯・全期間）</h2>
  <p class="cap">セルの濃さ＝assistant応答数（√スケール）。自分がいつ Claude を回してるかの地図</p>
  {_punch_html(data["punch"])}
</div>

<div class="card">
  <h2>🗓️ 案件 × 日（直近{HEATMAP_DAYS}日）</h2>
  <p class="cap">どの案件がいつ動いたか。濃さ＝その日の応答数</p>
  {_heat_html(data["heatmap"])}
</div>

<div class="card">
  <h2>🔢 案件別 出力トークン（全期間累計）</h2>
  <p class="cap">1本＝1案件。長さ＝トークン量、右は全体シェアとセッション本数</p>
  {_bars_html(data["tokens_by_project"])}
</div>

<div class="card">
  <h2>🧾 案件別 書いた行数（全期間累計）</h2>
  <p class="cap">1本＝1案件。長さ＝行数、色＝コード／ドキュメント／その他。週報・工数入力の裏付けに</p>
  {_loc_bars_html(data["loc"]["by_project"])}
</div>

<div class="card">
  <h2>🎯 案件マッピングの根拠（{t["matched"]}/{t["sessions"]} 本）</h2>
  <p class="cap">何を手がかりに案件へ紐付いたか＝この地図の信頼度そのもの</p>
  {_sources_html(t["match_sources"], t["matched"], t["unmatched"])}
</div>

<h2 class="sec">🗺️ 案件 × セッション地図</h2>
<p class="sub" style="margin:0 0 8px">最終稼働が新しい順・直近{RECENT_OPEN_DAYS}日に動いた案件は開いた状態</p>
{_projects_html(data["projects"])}
{_unmatched_html(data["unmatched"])}

<footer>
  再生成 <code>python claude_dashboard_gen.py</code> ／ 中間データ <code>reports/claude_dashboard_data.json</code>（L3統合用）<br>
  ライブ監視は <code>live_board.bat</code>／次のS番号は <code>python session_no.py "&lt;案件名&gt;"</code>／
  行数だけ端末で見るなら <code>python loc_scan.py [--days N]</code>
</footer>
</div>
<div id="tip"></div>
<script>{JS}</script>
</body>
</html>"""


def main():
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    print(f"config: {cfg['_config_path']}")
    print(f"scanning {claude_dir / 'projects'} ...")
    sessions = scan_all(
        claude_dir,
        cache_path=REPO_ROOT / "cache" / "scan_cache.json",
        workspaces=cfg.get("workspaces") or None,
        progress=print,
    )
    mapper = ProjectMapper(cfg)
    for s in sessions:
        mapper.map_session(s)

    data = build_data(sessions, cfg)

    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    json_path = report_dir / "claude_dashboard_data.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    html_path = report_dir / "claude_dashboard.html"
    html_path.write_text(render_html(data), encoding="utf-8")

    t = data["totals"]
    print(f"\ndata -> {json_path}")
    print(f"html -> {html_path}")
    print(f"sessions={t['sessions']} matched={t['matched']} "
          f"({t['matched'] / t['sessions'] * 100:.0f}%) projects={t['projects']} "
          f"unmatched={t['unmatched']}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    main()
