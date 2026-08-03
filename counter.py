"""エージェントカウンター（CLI）: 台帳の未消化📨＝依頼票を受け取る窓口。

台帳は pull 型＝読んだ瞬間のスナップショットでしかない。並行セッションが走っている最中に
「起動時に見た📨に無い」を根拠に届いてないと判断すると外す（実際に外した）。
作業の途中でも数秒で受信確認できるのがこのコマンドの役目。

使い方:
  python counter.py                 未消化の依頼票だけ全案件ぶん（＋回覧板）＝既定
  python counter.py "ライブボード"   その案件の現在地 / 依頼票 / Next / ブロッカー（別名可）
  python counter.py --all           全台帳の現在地1行＋📨件数（棚卸し）
  python counter.py --unresolved    案件名が引けなかった台帳（config.local.json 育成用）

正本は台帳そのもの。ここは毎回ファイルから読むだけで、内容のコピーは持たない。
"""
import io
import sys
import unicodedata
from pathlib import Path

from claude_sv.ledger import head_lines, load_bulletin, load_ledgers, find_ledgers
from claude_sv.mapping import ProjectMapper, load_config

REPO_ROOT = Path(__file__).parent
RULE = "─" * 62


def _w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def _indent(text: str, prefix: str = "   ") -> str:
    return "\n".join(prefix + ln for ln in text.splitlines()) if text else ""


def _clip(line: str, width: int) -> str:
    """表示幅で切る（申し送りは1行が数百字になることがある）。"""
    if _w(line) <= width:
        return line
    out, acc = [], 0
    for c in line:
        acc += 2 if unicodedata.east_asian_width(c) in "WFA" else 1
        if acc > width - 1:
            break
        out.append(c)
    return "".join(out) + "…"


def _quote(sec, max_lines: int = 0, width: int = 0) -> str:
    """申し送り本文。max_lines>0 なら要約表示（残り行数を添える）。"""
    body = head_lines(sec.body, max_lines) if max_lines else sec.body
    if width:
        body = "\n".join(_clip(ln, width) for ln in body.splitlines())
    out = _indent(body, "    ")
    rest = sec.n_lines - max_lines
    if max_lines and rest > 0:
        out += f"\n    …他{rest}行"
    return out


def _where(led) -> str:
    return f"{led.ws_label} / {led.path.name}"


def _load():
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    ws_labels = cfg.get("workspace_labels", {})
    mapper = ProjectMapper(cfg)
    return claude_dir, ws_labels, mapper, load_ledgers(claude_dir, ws_labels, mapper)


def show_bulletin(claude_dir, ws_labels):
    """回覧板（宛先なし・読めば終わり）。あれば必ず先頭に出す。"""
    for ws_label, path, body in load_bulletin(claude_dir, ws_labels):
        print(f"\n📋 回覧板［{ws_label}］  {path}")
        print(_indent(body, "   "))


def show_inbox_all(ledgers):
    hits = [(l, l.inbox()) for l in ledgers]
    hits = [(l, ss) for l, ss in hits if ss]
    n = sum(len(ss) for _, ss in hits)
    print(f"\n📨 未消化の依頼票 {n}件 / {len(hits)}案件（台帳 {len(ledgers)}本を確認）")
    if not hits:
        print("   （なし）宛先つきの申し送りは全部消化済み。")
        return
    for led, secs in hits:
        print(RULE)
        print(f"【{led.label}】  {_where(led)}")
        for s in secs:
            print(f"  ▸ {s.title}")
            print(_quote(s, max_lines=4, width=108))
    print(RULE)
    print('   全文は  python counter.py "<案件名>"')


def show_project(ledgers, query, mapper):
    """案件を指定して読む。1案件が両ワークスペースに台帳を持つことがあるので複数でも全部描く。"""
    hits = find_ledgers(ledgers, query, mapper)
    if not hits:
        print(f"\n❓ 「{query}」に対応する台帳が見つからない。")
        print("   候補: " + " / ".join(sorted({l.label for l in ledgers if l.project})))
        return 1
    if len(hits) > 4:
        print(f"\n❓ 「{query}」で {len(hits)}本ヒット。案件名を絞って:")
        for l in hits:
            print(f"   - {_pad(l.label, 24)} {_where(l)}")
        return 1
    if len(hits) > 1:
        print(f"\n（「{query}」に台帳 {len(hits)}本）")
    for led in hits:
        show_one(led)
    return 0


def show_one(led):
    print(f"\n🗂️ {led.label}   （{_where(led)}）")
    if led.description:
        print(f"   {led.description[:110]}")
    print(RULE)
    print("📍 現在地")
    print(_indent(led.current(5)) or "   （読み取れず）")
    inbox = led.inbox()
    print(f"\n📨 未消化の依頼票 {len(inbox)}件")
    for s in inbox:
        print(f"  ▸ {s.title}")
        print(_quote(s))
    if not inbox:
        print("   （なし）")
    nx, bl = led.next_up(4), led.blockers(3)
    if nx:
        print("\n▶️ Next")
        print(_indent(nx))
    if bl:
        print("\n🚧 ブロッカー / 判断待ち")
        print(_indent(bl))
    print(f"\n   正本: {led.path}")


def show_all(ledgers):
    print(f"\n🗂️ 台帳 {len(ledgers)}本")
    for led in sorted(ledgers, key=lambda l: (l.ws_label, l.label)):
        n = len(led.inbox())
        head = (led.current(1) or led.description or "").replace("**", "")
        print(f"{_pad(led.ws_label, 10)} {_pad(led.label, 22)} "
              f"{('📨' + str(n)) if n else '  ':4} {head[:70]}")


def show_unresolved(ledgers):
    bad = [l for l in ledgers if not l.project]
    print(f"\n🔍 案件名が引けなかった台帳 {len(bad)}本 / {len(ledgers)}本")
    print("   （config.local.json の project_aliases に別名を足せば案件に繋がる）")
    for led in bad:
        print(f"   - {_pad(led.path.stem, 34)} {_pad(led.ws_label, 10)} {led.description[:52]}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if a.strip()]
    claude_dir, ws_labels, mapper, ledgers = _load()

    if args and args[0] == "--unresolved":
        show_unresolved(ledgers)
        return 0
    if args and args[0] == "--all":
        show_bulletin(claude_dir, ws_labels)
        show_all(ledgers)
        return 0

    show_bulletin(claude_dir, ws_labels)
    if args:
        return show_project(ledgers, " ".join(args), mapper)
    show_inbox_all(ledgers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
