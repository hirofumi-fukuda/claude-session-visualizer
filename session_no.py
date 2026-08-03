"""採番ヘルパー: 案件ごとの「次に使う S番号」を出す。

セッションを「〈案件名〉 S〈n〉 開始」で始める運用（採番セッション運用ガイド）を
続けるには、開始時に n を即答できる必要がある。台帳を開いて数えるのは続かないので、
既存セッション（jsonl）から採番済みの最大 S を機械的に拾う。

使い方:
  python session_no.py                # 全案件の 最大S / セッション数 / 最終稼働 を一覧
  python session_no.py "UMA-SAGA"     # その案件の次のS番号を1行で（別名でも引ける）
"""
import io
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.scan import scan_all

REPO_ROOT = Path(__file__).parent


def _last(ts_iso):
    if not ts_iso:
        return "?"
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone().strftime("%m/%d")
    except ValueError:
        return "?"


def collect():
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    mapper = ProjectMapper(cfg)
    sessions = scan_all(claude_dir, cache_path=REPO_ROOT / "cache" / "scan_cache.json",
                        workspaces=cfg.get("workspaces") or None)
    stats = defaultdict(lambda: {"max_no": 0, "n": 0, "last": ""})
    for s in sessions:
        mapper.map_session(s)
        if not s.get("project"):
            continue
        st = stats[s["project"]]
        st["n"] += 1
        if s.get("session_no") is not None:
            st["max_no"] = max(st["max_no"], s["session_no"])
        last = s.get("last_ts") or s.get("end_ts") or s.get("start_ts") or ""
        if last > st["last"]:
            st["last"] = last
    return mapper, stats


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    mapper, stats = collect()
    if len(sys.argv) > 1:
        raw = " ".join(sys.argv[1:])
        name = mapper._resolve_project(raw)  # 別名→正式名（config.project_aliases）
        st = stats.get(name)
        nxt = (st["max_no"] + 1) if st else 1
        seen = f"（採番済み最大 S{st['max_no']} / 既存 {st['n']}セッション）" if st else "（新規案件）"
        print(f"{name} S{nxt} 開始   {seen}")
        return
    rows = sorted(stats.items(), key=lambda kv: kv[1]["last"], reverse=True)
    print(f"{'案件':<24}{'次のS':>7}{'採番済':>7}{'本数':>6}  最終")
    print("-" * 60)
    for name, st in rows:
        pad = 24 - sum(2 if ord(c) > 0x2E80 else 1 for c in name)
        print(f"{name}{' ' * max(1, pad)}{'S' + str(st['max_no'] + 1):>6}"
              f"{('S' + str(st['max_no'])) if st['max_no'] else '—':>7}{st['n']:>6}  {_last(st['last'])}")


if __name__ == "__main__":
    main()
