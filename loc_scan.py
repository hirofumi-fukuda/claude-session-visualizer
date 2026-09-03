"""書いた行数（LOC）を端末で見る CLI（アクティビティ・トラックレコードと同じ集計器の薄い入口）。

  python loc_scan.py            直近7日
  python loc_scan.py --days 30  直近30日
  python loc_scan.py --all      全期間

集計の定義は claude_sv/loc.py（Edit/Write/MultiEdit/NotebookEdit＋Bash/PowerShell のファイル直書き・
成功分のみ・サブエージェント含む・sed -i 等の間接編集は「未計測」として件数のみ）。
案件名はダッシュボードと同じ採番マッピング（ProjectMapper）＝「冒頭発話ラベル」ではなくトラック単位。
Anthropic 管理コンソールの「コード行数」とは別物＝社内比較用の自前指標。
"""
import argparse
import io
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.scan import scan_all

REPO_ROOT = Path(__file__).parent
UNCLASSIFIED = "（未分類）"
KINDS = ("code", "docs", "other")


def main():
    ap = argparse.ArgumentParser(description="Claude が書いた行数（日別・案件別・ファイル別）")
    ap.add_argument("--days", type=int, default=7, help="直近N日（既定7）")
    ap.add_argument("--all", action="store_true", help="全期間")
    ap.add_argument("--top", type=int, default=15, help="案件・ファイルの表示件数")
    args = ap.parse_args()

    cfg = load_config(REPO_ROOT)
    sessions = scan_all(Path(cfg.get("claude_dir", "~/.claude")).expanduser(),
                        cache_path=REPO_ROOT / "cache" / "scan_cache.json",
                        workspaces=cfg.get("workspaces") or None)
    mapper = ProjectMapper(cfg)
    cutoff = "" if args.all else (date.today() - timedelta(days=args.days - 1)).isoformat()

    by_day = defaultdict(lambda: defaultdict(int))
    by_proj = defaultdict(lambda: defaultdict(int))
    files = defaultdict(int)
    unmeasured = defaultdict(int)
    for s in sessions:
        mapper.map_session(s)
        p = s["project"] or UNCLASSIFIED
        if s["session_no"] is not None:
            p = f"{p} S{s['session_no']}"
        hit = False
        for d, v in (s.get("days_loc") or {}).items():
            if d < cutoff:
                continue
            hit = True
            for k in KINDS + ("removed",):
                by_day[d][k] += v.get(k, 0)
                by_proj[p][k] += v.get(k, 0)
        if hit:
            for name, n in (s.get("files_loc") or {}).items():
                files[name] += n
            unmeasured[p] += (s.get("loc") or {}).get("unmeasured", 0)

    def tot(r):
        return sum(r[k] for k in KINDS)

    label = "全期間" if args.all else f"直近{args.days}日（{cutoff}〜）"
    print(f"=== 日別（{label}） ===")
    print(f"{'date':<12}{'+行':>8}{'code':>8}{'docs':>8}{'other':>7}{'削除':>8}")
    for d in sorted(by_day):
        r = by_day[d]
        print(f"{d:<12}{tot(r):>8,}{r['code']:>8,}{r['docs']:>8,}{r['other']:>7,}{r['removed']:>8,}")
    g = {k: sum(r[k] for r in by_day.values()) for k in KINDS + ("removed",)}
    gt = sum(g[k] for k in KINDS) or 1
    print(f"{'計':<12}{gt:>8,}{g['code']:>8,}{g['docs']:>8,}{g['other']:>7,}{g['removed']:>8,}"
          f"   code {g['code'] / gt * 100:.0f}% / docs {g['docs'] / gt * 100:.0f}%")

    print(f"\n=== 案件×S（上位{args.top}） ===")
    print(f"{'+行':>8}{'code':>8}{'docs':>8}{'削除':>7}{'未計測':>7}  案件")
    for p, r in sorted(by_proj.items(), key=lambda kv: -tot(kv[1]))[:args.top]:
        print(f"{tot(r):>8,}{r['code']:>8,}{r['docs']:>8,}{r['removed']:>7,}{unmeasured[p]:>7}  {p}")

    print(f"\n=== ファイル（上位{args.top}） ===")
    for name, n in sorted(files.items(), key=lambda kv: -kv[1])[:args.top]:
        print(f"{n:>8,}  {name}")
    print("\n※ 成功した編集のみ・サブエージェント含む。sed -i / python直書き等は「未計測」＝件数のみ。")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
