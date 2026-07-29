"""L0 棚卸しレポート: 案件×セッション地図を Markdown で生成する。

使い方:  python l0_report.py
出力:    reports/L0_案件セッション地図_YYYYMMDD.md（gitignore対象・ローカルのみ）
"""
import io
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.scan import scan_all

REPO_ROOT = Path(__file__).parent


def _fmt_date(ts_iso):
    if not ts_iso:
        return "?"
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone().strftime("%m/%d %H:%M")
    except ValueError:
        return "?"


def _snippet(s, limit=60):
    s = (s or "").replace("\n", " ").replace("|", "／")
    return s[:limit] + ("…" if len(s) > limit else "")


def main():
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    ws_labels = cfg.get("workspace_labels", {})
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

    matched = [s for s in sessions if s["project"]]
    unmatched = [s for s in sessions if not s["project"]]

    by_project = defaultdict(list)
    for s in matched:
        by_project[s["project"]].append(s)
    for lst in by_project.values():
        lst.sort(key=lambda s: (s["session_no"] is None, s["session_no"] or 0, s["first_ts"] or ""))

    # 案件は最終稼働の新しい順
    projects = sorted(by_project, key=lambda p: max(s["last_ts"] or "" for s in by_project[p]), reverse=True)

    today = datetime.now().astimezone().date()
    days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]

    out = []
    w = out.append
    w(f"# L0 案件×セッション地図（{today.strftime('%Y-%m-%d')} 生成）\n")
    total_mb = sum(s["size_bytes"] for s in sessions) / 1024 / 1024
    total_out_tok = sum(s["output_tokens"] for s in sessions)
    w(f"- 対象: {len(sessions)} セッション / {total_mb:,.0f} MB / 出力トークン累計 {total_out_tok/1000:,.0f}k")
    w(f"- 案件マッピング: ✅ {len(matched)} 本（{len(by_project)} 案件） / ❓ 未分類 {len(unmatched)} 本")
    src_counts = defaultdict(int)
    for s in matched:
        src_counts[s["match_source"]] += 1
    w(f"- 抽出ソース内訳: {dict(src_counts)}\n")

    w("## 案件×セッション地図\n")
    for p in projects:
        lst = by_project[p]
        span_last = max(s["last_ts"] or "" for s in lst)
        w(f"### {p}（{len(lst)}セッション・最終稼働 {_fmt_date(span_last)}）\n")
        w("| S | 開始 | 最終稼働 | 発話 | 応答 | ツール | sub-ag | 出力tok(k) | WS |")
        w("|---|---|---|---|---|---|---|---|---|")
        for s in lst:
            sn = f"S{s['session_no']}" if s["session_no"] is not None else "—"
            ws = ws_labels.get(s["workspace"], s["workspace"][:16])
            w(f"| {sn} | {_fmt_date(s['first_ts'])} | {_fmt_date(s['last_ts'])} "
              f"| {s['n_user_prompts']} | {s['n_assistant']} | {s['n_tool_use']} "
              f"| {s.get('n_subagents', 0)} | {s['output_tokens']/1000:,.0f} | {ws} |")
        w("")

    w("## 日別稼働（直近14日 × 案件・assistant応答数）\n")
    active = defaultdict(lambda: defaultdict(int))
    for s in sessions:
        p = s["project"] or "（未分類）"
        for day, n in s["days_active"].items():
            if day in days:
                active[p][day] += n
    rows = sorted(active, key=lambda p: -sum(active[p].values()))
    w("| 案件 | " + " | ".join(d[5:] for d in days) + " | 計 |")
    w("|---|" + "---|" * (len(days) + 1))
    for p in rows:
        cells = [str(active[p][d]) if active[p][d] else "·" for d in days]
        w(f"| {p} | " + " | ".join(cells) + f" | {sum(active[p].values())} |")
    w("")

    w(f"## 未分類セッション（{len(unmatched)}本・新しい順・上位40）\n")
    w("| 最終稼働 | タイトル/冒頭発話 | 発話 | サイズKB | WS |")
    w("|---|---|---|---|---|")
    for s in sorted(unmatched, key=lambda s: s["last_ts"] or "", reverse=True)[:40]:
        label = s["ai_title"] or s["first_user_text"] or s["last_prompt"] or "(発話なし)"
        ws = ws_labels.get(s["workspace"], s["workspace"][:16])
        w(f"| {_fmt_date(s['last_ts'])} | {_snippet(label)} | {s['n_user_prompts']} "
          f"| {s['size_bytes']//1024} | {ws} |")
    w("")

    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    out_path = report_dir / f"L0_案件セッション地図_{today.strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"\nreport -> {out_path}")
    print(f"matched {len(matched)}/{len(sessions)} ({len(matched)/len(sessions)*100:.0f}%), "
          f"projects={len(by_project)}, unmatched={len(unmatched)}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    main()
