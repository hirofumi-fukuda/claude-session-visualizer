"""エージェントカウンター（HTML）: 全台帳の「現在地＋未消化の依頼票」を1画面に。

司令塔定例のアジェンダ「全台帳棚卸し」を人力レンダリングから置き換える窓口。
正本は台帳ファイルのまま＝ここは毎回ファイルから描き直すだけで内容を持たない。
外部リソースはゼロ（CDN禁止・ローカル完結）。`~/.claude` へは読み取りのみ。

  python counter_gen.py            reports/counter.html と reports/counter/ を書き出して開く
  python counter_gen.py --no-open  生成だけ

出力:
  reports/counter.html          全体ビュー（回覧板→依頼票→案件カード→その他の台帳）
  reports/counter/<slug>.html   台帳の全文レンダリング（ライブボード/マップの行リンク先＝②）
"""
import html as html_mod
import io
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from claude_sv.ledger import load_bulletin, load_ledgers, parse_frontmatter, slug
from claude_sv.mapping import ProjectMapper, load_config

REPO_ROOT = Path(__file__).parent
OUT_DIR = REPO_ROOT / "reports"
PAGE_DIR = OUT_DIR / "counter"

CSS = """
*{box-sizing:border-box}
:root{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10); --wash:rgba(255,255,255,.055);
  --cat-1:#3987e5; --mail:#e5a13a; --board:#57b364;
}
:root[data-theme="light"]{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10); --wash:rgba(11,11,11,.045);
  --cat-1:#2a78d6; --mail:#b4741c; --board:#3d8a4b;
}
body{margin:0;padding:22px 20px 60px;background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;
  font-size:13px;line-height:1.62;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.01em}
.sub{color:var(--muted);font-size:11.5px;margin-top:3px}
.nav{display:flex;gap:8px;align-items:center}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;
  padding:5px 13px;font-size:11.5px;cursor:pointer;font-family:inherit;white-space:nowrap;
  text-decoration:none;display:inline-block}
.tgl:hover{background:var(--wash);color:var(--ink)}
h2{font-size:12.5px;font-weight:600;color:var(--ink2);margin:30px 0 11px;
  letter-spacing:.04em;display:flex;align-items:center;gap:9px}
h2 .n{color:var(--muted);font-weight:400;font-size:11px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:11px;
  padding:13px 16px;margin-bottom:11px}
.card.mail{border-left:3px solid var(--mail)}
.card.bull{border-left:3px solid var(--board)}
.ttl{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:7px}
.ttl a,.ttl strong{font-size:13.5px;font-weight:600;color:var(--ink);text-decoration:none}
.ttl a:hover{text-decoration:underline}
.where{color:var(--muted);font-size:11px;font-family:ui-monospace,Consolas,monospace}
.badge{font-size:10.5px;color:var(--mail);border:1px solid var(--mail);border-radius:999px;
  padding:0 7px;white-space:nowrap}
.desc{color:var(--muted);font-size:11.5px;margin:-2px 0 8px}
.sec{margin-top:9px}
.sec>.h{font-size:11px;color:var(--muted);margin-bottom:3px}
.md{color:var(--ink2);font-size:12.5px}
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
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(345px,1fr));gap:11px}
.grid .card{margin:0}
details{margin-top:8px}
summary{cursor:pointer;color:var(--muted);font-size:11.5px;padding:5px 0}
.empty{color:var(--muted);font-size:11.5px;padding:10px 2px}
footer{color:var(--muted);font-size:11px;margin-top:34px;border-top:1px solid var(--border);
  padding-top:11px}
"""
HEAD_JS = "try{var t=localStorage.getItem('csv-theme');" \
          "if(t)document.documentElement.dataset.theme=t;}catch(e){}"
TGL_JS = """
(function(){
  var root=document.documentElement, btn=document.getElementById('tgl');
  function lab(){btn.textContent = root.dataset.theme==='light' ? '☾ ダーク' : '☀ ライト';}
  lab();
  btn.onclick=function(){
    root.dataset.theme = root.dataset.theme==='light' ? 'dark' : 'light';
    try{localStorage.setItem('csv-theme', root.dataset.theme);}catch(e){}
    lab();
  };
})();
"""


# ── markdown（台帳で実際に使われている記法だけ・外部ライブラリなし）────────────
def _inline(s: str) -> str:
    s = html_mod.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r'<span class="wiki">[[\1]]</span>', s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    # 裸URLの自動リンク。エスケープ済みなので &lt; &gt; &quot; の手前で止める（&amp; はURLの一部）
    s = re.sub(r"(?<![\"'>=])(https?://(?:(?!&(?:lt|gt|quot);)[^\s<）)])+)",
               r'<a href="\1">\1</a>', s)
    return s


def _md(text: str) -> str:
    """台帳本文を HTML に。段落 / 箇条書き（ネスト）/ 表 / コードブロック / 見出し / 罫線。"""
    out, lines, i = [], (text or "").splitlines(), 0
    stack, para = [], []      # stack: 開いている <ul> のインデント深さ

    def close_lists(to: int = 0):
        while len(stack) > to:
            stack.pop()
            out.append("</ul>")

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):                                   # コードブロック
            flush_para(); close_lists()
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            out.append("<pre>" + html_mod.escape("\n".join(buf)) + "</pre>")
            i += 1
            continue
        if not s:
            flush_para(); close_lists()
            i += 1
            continue
        if s.startswith("|") and s.endswith("|"):                  # 表
            flush_para(); close_lists()
            rows, i = [], i
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            if rows:
                out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                           + "</tr>")
                for r in rows[1:]:
                    out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                out.append("</table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para(); close_lists()
            out.append(f"<h{min(len(m.group(1)) + 2, 4)}>{_inline(m.group(2))}"
                       f"</h{min(len(m.group(1)) + 2, 4)}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush_para(); close_lists()
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^([-*・]|\d+\.)\s+(.*)$", s)
        if m:
            flush_para()
            depth = (len(ln) - len(ln.lstrip())) // 2 + 1
            while len(stack) > depth:
                close_lists(len(stack) - 1)
            while len(stack) < depth:
                stack.append(depth); out.append("<ul>")
            out.append(f"<li>{_inline(m.group(2))}</li>")
            i += 1
            continue
        if stack and out and out[-1].endswith("</li>"):   # 箇条書きの折り返し行
            out[-1] = out[-1][:-len("</li>")] + "<br>" + _inline(s) + "</li>"
        elif stack:
            out.append(f"<li>{_inline(s)}</li>")
        else:
            para.append(s)
        i += 1
    flush_para(); close_lists()
    return "\n".join(out)


# ── ページ組み立て ─────────────────────────────────────────────────
def _page(title: str, body: str, up: str = "") -> str:
    nav = f'<a class="tgl" href="{up}">← カウンター</a>' if up else (
        '<a class="tgl" href="live_board.html">🖥️ ライブボード</a>'
        '<a class="tgl" href="claude_dashboard.html">🗺️ エージェントマップ</a>')
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html_mod.escape(title)}</title><script>{HEAD_JS}</script>'
            f'<style>{CSS}</style></head><body><div class="wrap">'
            f'<header><div><h1>{html_mod.escape(title)}</h1>'
            f'<div class="sub">正本は台帳ファイル本体。この画面は毎回そこから描き直しているだけ'
            f'（内容のコピーは持たない）　生成 {datetime.now():%m/%d %H:%M}</div></div>'
            f'<div class="nav">{nav}<button class="tgl" id="tgl"></button></div></header>'
            f'{body}</div><script>{TGL_JS}</script></body></html>')


def _where(led) -> str:
    return f"{led.ws_label} / {led.path.name}"


def page_name(led) -> str:
    """台帳ページのファイル名。案件が引けていれば案件単位（同案件の複数台帳を1枚に）。"""
    return (slug(led.project, "p-") if led.project else slug(led.path.stem, "f-")) + ".html"


def _inbox_html(led, link: bool = True) -> str:
    secs = led.inbox()
    ttl = (f'<a href="counter/{page_name(led)}">{html_mod.escape(led.label)}</a>' if link
           else f"<strong>{html_mod.escape(led.label)}</strong>")
    body = "".join(f'<div class="sec"><div class="h">{html_mod.escape(s.title)}</div>'
                   f'<div class="md">{_md(s.body)}</div></div>' for s in secs)
    return (f'<div class="card mail"><div class="ttl">{ttl}'
            f'<span class="badge">依頼票 {len(secs)}</span>'
            f'<span class="where">{html_mod.escape(_where(led))}</span></div>{body}</div>')


def _card_html(led) -> str:
    n = len(led.inbox())
    badge = f'<span class="badge">📨 {n}</span>' if n else ""
    nx = led.next_up(2)
    nxt = (f'<div class="sec"><div class="h">▶️ Next</div>'
           f'<div class="md">{_md(nx)}</div></div>') if nx else ""
    return (f'<div class="card"><div class="ttl">'
            f'<a href="counter/{page_name(led)}">{html_mod.escape(led.label)}</a>{badge}'
            f'<span class="where">{html_mod.escape(_where(led))}</span></div>'
            f'<div class="md">{_md(led.current(4))}</div>{nxt}</div>')


def build(open_browser: bool = True) -> Path:
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    ws_labels = cfg.get("workspace_labels", {})
    mapper = ProjectMapper(cfg)
    ledgers = load_ledgers(claude_dir, ws_labels, mapper)

    body = []
    for ws_label, path, text in load_bulletin(claude_dir, ws_labels):
        body.append(f'<h2>📋 回覧板<span class="n">{html_mod.escape(ws_label)}</span></h2>'
                    f'<div class="card bull"><div class="md">{_md(text)}</div>'
                    f'<div class="where">{html_mod.escape(str(path))}</div></div>')

    mails = [l for l in ledgers if l.inbox()]
    n_mail = sum(len(l.inbox()) for l in mails)
    body.append(f'<h2>📨 未消化の依頼票<span class="n">{n_mail}件 / {len(mails)}案件</span></h2>')
    body.append("".join(_inbox_html(l) for l in mails) if mails
                else '<div class="empty">（なし）宛先つきの申し送りは全部消化済み。</div>')

    named = [l for l in ledgers if l.project]
    rest = [l for l in ledgers if not l.project]
    for ws_label in ws_labels.values():
        ws = [l for l in named if l.ws_label == ws_label]
        if ws:
            body.append(f'<h2>🗂️ {html_mod.escape(ws_label)} の案件'
                        f'<span class="n">{len(ws)}本</span></h2>'
                        f'<div class="grid">{"".join(_card_html(l) for l in ws)}</div>')
    if rest:
        rows = "".join(
            f'<div><a href="counter/{page_name(l)}">{html_mod.escape(l.path.stem)}</a>'
            f' <span class="where">{html_mod.escape(l.ws_label)}</span> '
            f'<span class="desc">{html_mod.escape(l.description[:90])}</span></div>'
            for l in rest)
        body.append(f'<h2>📁 案件名が引けていない台帳<span class="n">{len(rest)}本</span></h2>'
                    f'<details><summary>開く（config.local.json の project_aliases に'
                    f'別名を足せば案件カードに昇格する）</summary>'
                    f'<div class="card md">{rows}</div></details>')
    body.append(f'<footer>台帳 {len(ledgers)}本を走査（frontmatter の type: project）。'
                f'依頼票は「📨」見出しの欄だけを数える＝標準セクション規約に揃っていない台帳は'
                f'先頭数行だけの表示になる。'
                f'CLI: <code>python counter.py "&lt;案件名&gt;"</code></footer>')

    OUT_DIR.mkdir(exist_ok=True)
    PAGE_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "counter.html"
    out.write_text(_page("🎫 エージェントカウンター", "".join(body)), encoding="utf-8")

    # 台帳ページ（同じ案件名の台帳は1枚にまとめる＝行リンクの宛先が案件単位で決まる）
    pages = {}
    for led in ledgers:
        pages.setdefault(page_name(led), []).append(led)
    for fname, group in pages.items():
        chunks = []
        for led in group:
            full = parse_frontmatter(led.path.read_text(encoding="utf-8"))[1]
            chunks.append(
                f'<h2>{html_mod.escape(led.path.name)}'
                f'<span class="n">{html_mod.escape(led.ws_label)}</span></h2>'
                f'<div class="card"><div class="desc">'
                f'{html_mod.escape(led.description)}</div>'
                f'<div class="md">{_md(full)}</div>'
                f'<div class="where">正本: {html_mod.escape(str(led.path))}</div></div>')
        (PAGE_DIR / fname).write_text(
            _page(f"🗂️ {group[0].label}", "".join(chunks), up="../counter.html"),
            encoding="utf-8")

    print(f"✅ {out}  （台帳 {len(ledgers)}本 / ページ {len(pages)}枚 / 依頼票 {n_mail}件）")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    build(open_browser="--no-open" not in sys.argv)


if __name__ == "__main__":
    main()
