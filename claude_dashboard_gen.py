"""L1 静的ダッシュボード: 案件×セッション地図・日別稼働・トークンを HTML 1枚に生成する。

使い方:  python claude_dashboard_gen.py
出力:    reports/claude_dashboard.html      … ブラウザで開く1枚もの（gitignore対象・ローカルのみ）
         reports/claude_dashboard_data.json … 中間データ（L3 業務ダッシュボード統合用）

生成物にトランスクリプト本文（発話・応答テキスト）は含めない（docs/01-設計方針.md）。
未分類セッションの識別は ai-title のみ。本文スニペットが必要なら l0_report.py を使う。
"""
import html
import io
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.scan import scan_all

REPO_ROOT = Path(__file__).parent
HEATMAP_DAYS = 14
HEATMAP_MAX_ROWS = 20
RECENT_OPEN_DAYS = 7  # この日数以内に稼働した案件は地図を開いた状態で表示
TOKEN_BAR_ROWS = 15
UNCLASSIFIED = "（未分類）"

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
            "output_tokens": 0, "workspaces": set(),
        })
        b["n_files"] += 1
        if s["first_ts"] and (b["first_ts"] is None or s["first_ts"] < b["first_ts"]):
            b["first_ts"] = s["first_ts"]
        if s["last_ts"] and (b["last_ts"] is None or s["last_ts"] > b["last_ts"]):
            b["last_ts"] = s["last_ts"]
        for k in ("n_user_prompts", "n_assistant", "n_tool_use", "output_tokens"):
            b[k] += s[k]
        b["n_subagents"] += s.get("n_subagents", 0)
        b["workspaces"].add(ws_labels.get(s["workspace"], s["workspace"]))

    by_project = defaultdict(list)
    for (proj, _), b in bundles.items():
        b["workspaces"] = sorted(b["workspaces"])
        by_project[proj].append(b)

    projects = []
    for name, blist in by_project.items():
        blist.sort(key=lambda b: (b["session_no"] is None, b["session_no"] or 0,
                                  b["first_ts"] or ""))
        projects.append({
            "name": name,
            "last_ts": max(b["last_ts"] or "" for b in blist),
            "n_bundles": len(blist),
            "n_files": sum(b["n_files"] for b in blist),
            "output_tokens": sum(b["output_tokens"] for b in blist),
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

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {
            "sessions": len(sessions),
            "size_mb": round(sum(s["size_bytes"] for s in sessions) / 1048576, 1),
            "output_tokens": sum(s["output_tokens"] for s in sessions),
            "subagents": sum(s.get("n_subagents", 0) for s in sessions),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "projects": len(by_project),
            "match_sources": dict(src_counts),
        },
        "heatmap": {"days": days, "rows": heat_rows},
        "projects": projects,
        "tokens_by_project": token_rows,
        "unmatched": unmatched_rows,
    }


# ---- HTML 生成（dashboard_gen.py と同型・自己完結1枚・外部ライブラリなし） ----

def _stat(n, label, color="#1c2833"):
    return (f'<div class="stat"><div class="n" style="color:{color}">{n}</div>'
            f'<div class="l">{esc(label)}</div></div>')


def _heatmap_html(heat):
    days, rows = heat["days"], heat["rows"][:HEATMAP_MAX_ROWS]
    peak = max((c for r in rows for c in r["counts"]), default=1) or 1
    head = []
    for d in days:
        wd = date.fromisoformat(d).weekday()
        cls = ' class="wk"' if wd >= 5 else ""
        head.append(f"<th{cls}>{int(d[5:7])}/{int(d[8:10])}</th>")
    body = []
    for r in rows:
        cells = []
        for d, c in zip(days, r["counts"]):
            if c:
                alpha = 0.12 + 0.88 * c / peak
                cells.append(f'<td class="hm" style="background:rgba(39,174,96,{alpha:.2f})" '
                             f'title="{esc(r["project"])} {d}: {c}">{c}</td>')
            else:
                cells.append('<td class="hm zero">·</td>')
        body.append(f'<tr><th class="pn">{esc(r["project"])}</th>{"".join(cells)}'
                    f'<td class="tot">{r["total"]}</td></tr>')
    dropped = len(heat["rows"]) - len(rows)
    note = (f'<p class="note">※ 表示は上位{HEATMAP_MAX_ROWS}案件（{dropped}案件省略）</p>'
            if dropped > 0 else "")
    return (f'<table class="heat"><tr><th></th>{"".join(head)}<td class="tot">計</td></tr>'
            f'{"".join(body)}</table>{note}')


def _token_bars_html(token_rows):
    rows = token_rows[:TOKEN_BAR_ROWS]
    peak = max((r["output_tokens"] for r in rows), default=1) or 1
    out = []
    for r in rows:
        w = max(1.5, r["output_tokens"] / peak * 100)
        out.append(
            f'<div class="bar-row"><div class="bar-label">{esc(r["project"])}</div>'
            f'<div class="bar-track"><div class="bar" style="width:{w:.1f}%"></div></div>'
            f'<div class="bar-val">{r["output_tokens"] / 1000:,.0f}k'
            f'<span class="bar-sub">／{r["n_sessions"]}本</span></div></div>')
    dropped = len(token_rows) - len(rows)
    if dropped > 0:
        rest = sum(r["output_tokens"] for r in token_rows[TOKEN_BAR_ROWS:])
        out.append(f'<p class="note">※ ほか{dropped}案件・計 {rest / 1000:,.0f}k</p>')
    return "".join(out)


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
            rows.append(
                f"<tr><td>{sn}</td><td>{esc(_span(b['first_ts'], b['last_ts']))}</td>"
                f"<td>{b['n_user_prompts']}</td><td>{b['n_assistant']}</td>"
                f"<td>{b['n_tool_use']}</td><td>{b['n_subagents'] or '·'}</td>"
                f"<td>{b['output_tokens'] / 1000:,.0f}</td>"
                f"<td>{esc('、'.join(b['workspaces']))}</td></tr>")
        out.append(f"""<details class="proj"{is_open}>
<summary><b>{esc(p["name"])}</b><span class="meta">{p["n_bundles"]}セッション
（{p["n_files"]}ファイル）・最終 {_fmt(p["last_ts"])}・出力 {p["output_tokens"] / 1000:,.0f}k</span></summary>
<table class="sess"><tr><th>S</th><th>期間</th><th>発話</th><th>応答</th><th>ツール</th>
<th>sub-ag</th><th>出力tok(k)</th><th>WS</th></tr>{"".join(rows)}</table>
</details>""")
    return "".join(out)


def _unmatched_html(unmatched):
    rows = []
    for u in unmatched:
        title = u["ai_title"] or "（無題）"
        rows.append(f"<tr><td>{_fmt(u['last_ts'])}</td><td>{esc(title)}</td>"
                    f"<td>{u['n_user_prompts']}</td><td>{u['size_kb']:,}</td>"
                    f"<td>{esc(u['workspace'])}</td></tr>")
    return f"""<details class="proj">
<summary><b>❓ 未分類セッション</b><span class="meta">{len(unmatched)}本
（識別は ai-title のみ・本文スニペットは l0_report.py で）</span></summary>
<table class="sess"><tr><th>最終稼働</th><th>タイトル</th><th>発話</th><th>KB</th><th>WS</th></tr>
{"".join(rows)}</table>
</details>"""


def render_html(data):
    t = data["totals"]
    match_pct = t["matched"] / t["sessions"] * 100 if t["sessions"] else 0
    gen = _fmt(data["generated_at"], "%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude セッションダッシュボード — {gen}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", "Yu Gothic", "Meiryo", sans-serif;
         margin: 0; padding: 24px; background: #f4f6f8; color: #1c2833; }}
  header {{ margin-bottom: 16px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .meta-line {{ color: #888; font-size: 13px; }}
  .summary {{ display: flex; gap: 16px; margin: 16px 0; flex-wrap: wrap; }}
  .stat {{ background: #fff; border-radius: 8px; padding: 12px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .stat .n {{ font-size: 26px; font-weight: bold; }}
  .stat .l {{ font-size: 12px; color: #888; }}
  .card {{ background: #fff; border-radius: 8px; padding: 16px 18px; margin: 16px 0;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .card h2 {{ font-size: 15px; margin: 0 0 12px; }}
  .note {{ color: #999; font-size: 12px; margin: 8px 0 0; }}
  table.heat {{ border-collapse: collapse; font-size: 12px; }}
  table.heat th {{ font-weight: normal; color: #666; padding: 3px 6px; text-align: center; }}
  table.heat th.wk {{ color: #c0392b; }}
  table.heat th.pn {{ text-align: left; max-width: 220px; overflow: hidden;
                      text-overflow: ellipsis; white-space: nowrap; color: #1c2833; }}
  table.heat td.hm {{ width: 34px; text-align: center; padding: 3px 0; border-radius: 3px; }}
  table.heat td.zero {{ color: #ccc; }}
  table.heat td.tot {{ padding: 3px 8px; color: #888; text-align: right; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 4px 0; font-size: 13px; }}
  .bar-label {{ width: 200px; text-align: right; overflow: hidden; text-overflow: ellipsis;
                white-space: nowrap; flex-shrink: 0; }}
  .bar-track {{ flex: 1; }}
  .bar {{ height: 14px; background: #2980b9; border-radius: 3px; min-width: 2px; }}
  .bar-val {{ width: 130px; flex-shrink: 0; color: #555; }}
  .bar-sub {{ color: #aaa; font-size: 11px; }}
  details.proj {{ background: #fff; border-radius: 8px; padding: 10px 18px; margin: 8px 0;
                  box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  details.proj summary {{ cursor: pointer; font-size: 14px; }}
  details.proj summary .meta {{ color: #888; font-size: 12px; margin-left: 10px; }}
  table.sess {{ border-collapse: collapse; font-size: 12.5px; margin: 10px 0 6px; }}
  table.sess th {{ text-align: left; color: #666; font-weight: normal; padding: 3px 12px 3px 0;
                   border-bottom: 1px solid #e3e8ec; }}
  table.sess td {{ padding: 4px 12px 4px 0; border-bottom: 1px solid #f0f3f5; }}
  .xf {{ color: #e67e22; font-size: 11px; }}
  footer {{ margin-top: 24px; color: #aaa; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <h1>📊 Claude セッションダッシュボード</h1>
  <div class="meta-line">生成: {gen} ／ 対象: ~/.claude/projects（{t["size_mb"]:,.0f} MB）</div>
</header>

<div class="summary">
  {_stat(t["sessions"], "セッション")}
  {_stat(t["projects"], "案件", "#2980b9")}
  {_stat(f"{match_pct:.0f}%", f"採番マッチ（{t['matched']}/{t['sessions']}）", "#27ae60")}
  {_stat(t["unmatched"], "未分類", "#c0392b")}
  {_stat(f"{t['output_tokens'] / 1000:,.0f}k", "出力トークン累計", "#8e44ad")}
  {_stat(t["subagents"], "サブエージェント", "#16a085")}
</div>

<div class="card">
  <h2>🗓️ 日別稼働（直近{HEATMAP_DAYS}日 × 案件・assistant応答数）</h2>
  {_heatmap_html(data["heatmap"])}
</div>

<div class="card">
  <h2>🔢 案件別 出力トークン（全期間累計・上位{TOKEN_BAR_ROWS}）</h2>
  {_token_bars_html(data["tokens_by_project"])}
</div>

<h2 style="font-size:16px;margin:20px 0 4px;">🗺️ 案件×セッション地図（最終稼働が新しい順・直近{RECENT_OPEN_DAYS}日稼働は展開表示）</h2>
{_projects_html(data["projects"])}
{_unmatched_html(data["unmatched"])}

<footer>
  抽出ソース内訳: {esc(str(t["match_sources"]))} ／
  再生成: <code>python claude_dashboard_gen.py</code> ／
  中間データ: <code>reports/claude_dashboard_data.json</code>（L3 業務ダッシュボード統合用）
</footer>
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
