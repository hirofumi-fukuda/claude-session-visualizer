"""今日の業務日誌（daily/YYYY-MM-DD.md）→ reports/daily.html（S20 段階1）。

- ファイル名は固定（daily.html）＝日付が変わってもタブを開き直さない。日付は書き出し時に決める
- live_board.py --html --watch のループに相乗りして毎 tick 呼ばれる（write_daily）。
  中身が変わった時だけ書く＝ページ側は stamp（daily_stamp.js）を数秒おきに読んで、変わった時だけ
  リロードする（file:// では fetch 不可なので <script src> の差し込みで読む）
- 見出し（##/###/####）ごとに <details> で折りたたみ。最初から開く見出しは config の
  daily_view.open（部分一致・"親 > 子" で親子指定）。開閉の手動操作は日付ごとに localStorage へ保存
- 相対リンクは daily ディレクトリ基準で file:/// の絶対 URI に解決

段階2（ブラウザから ⬜→✅）に備えて、変換（render_daily）は I/O から切り離した純関数にしてある。
S23: 末尾の board_summary / load_board＝office.html の休憩室ホワイトボードに書く要点（TOP 見出し・⏰ 縛り・返信待ち）。
"""
import hashlib
import html as html_mod
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

from claude_sv.mdlite import _inline, render as md_render
from claude_sv.nav import nav_html

DEFAULT_OPEN = ["最重要", "Today's Focus", "本日の予定 > TOP", "返信待ち"]
WEEKDAY = "月火水木金土日"

CSS = """
*{box-sizing:border-box}
:root{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10); --wash:rgba(255,255,255,.055); --cat-1:#3987e5;
}
:root[data-theme="light"]{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10); --wash:rgba(11,11,11,.045); --cat-1:#2a78d6;
}
body{margin:0;padding:22px 20px 60px;background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;
  font-size:13.5px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{font-size:18px;font-weight:600;margin:0}
.sub{color:var(--muted);font-size:11.5px;margin-top:3px}
.sub b{color:var(--ink2);font-weight:600;font-variant-numeric:tabular-nums}
.navs{display:flex;gap:6px;flex-wrap:wrap}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;
  padding:5px 13px;font-size:11.5px;cursor:pointer;font-family:inherit;white-space:nowrap;
  text-decoration:none;display:inline-block}
.tgl:hover{background:var(--wash);color:var(--ink)}
.tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
.bar{display:flex;gap:6px;margin:14px 0 10px}
.bar button{font-size:11px;padding:3px 10px}
details{border-left:2px solid var(--grid);margin:4px 0 4px 0;padding-left:12px}
details.l2{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:6px 14px;margin:10px 0}
summary{cursor:pointer;font-weight:600;padding:3px 0;list-style-position:outside}
summary::marker{color:var(--muted)}
details.l2>summary{font-size:15px}
details.l3>summary{font-size:14px}
details.l4>summary{font-size:13.5px;color:var(--ink2)}
.cnt{font-weight:400;font-size:11px;color:var(--muted);margin-left:8px;font-variant-numeric:tabular-nums}
.body{padding:2px 0 4px}
.body p{margin:6px 0}
ul{margin:4px 0;padding-left:22px}
li{margin:2px 0}
a{color:var(--cat-1)}
code{background:var(--wash);border-radius:4px;padding:0 5px;font-size:12px}
pre{background:var(--wash);border-radius:8px;padding:10px 12px;overflow:auto;font-size:12px}
blockquote{margin:8px 0;padding:6px 12px;border-left:3px solid var(--cat-1);background:var(--wash);
  border-radius:0 8px 8px 0}
blockquote h3,blockquote h4{margin:8px 0 4px;font-size:13.5px}
table{border-collapse:collapse;margin:6px 0;font-size:12.5px;display:block;overflow-x:auto}
th,td{border:1px solid var(--border);padding:4px 8px;vertical-align:top;text-align:left}
th{background:var(--wash)}
.wiki{color:var(--ink2)}
.missing{background:var(--surface);border:1px solid var(--border);border-radius:11px;
  padding:18px;color:var(--ink2);margin-top:16px}
footer{margin-top:24px;color:var(--muted);font-size:11px}
@media (max-width:640px){body{padding:16px 16px 48px}}
"""

HEAD_JS = "try{var t=localStorage.getItem('csv-theme');" \
          "if(t)document.documentElement.dataset.theme=t;}catch(e){}"

# STAMP / DAY / POLL_MS はサーバ側で埋める
PAGE_JS = """
(function(){
  var root=document.documentElement, btn=document.getElementById('tgl');
  function lab(){btn.textContent = root.dataset.theme==='light' ? '☾ ダーク' : '☀ ライト';}
  lab();
  btn.onclick=function(){
    root.dataset.theme = root.dataset.theme==='light' ? 'dark' : 'light';
    try{localStorage.setItem('csv-theme', root.dataset.theme);}catch(e){}
    lab();
  };
  // 時計
  var clk=document.getElementById('clock'), W='日月火水木金土';
  function tick(){var d=new Date(), p=function(n){return ('0'+n).slice(-2);};
    clk.textContent=(d.getMonth()+1)+'/'+d.getDate()+'（'+W[d.getDay()]+'） '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}
  tick(); setInterval(tick,1000);
  // 開閉の手動操作を日付ごとに保存（既定は生成側の open 指定）
  var KEY='daily-open:'+DAY, st={};
  try{st=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
  var ds=document.querySelectorAll('details[data-k]');
  ds.forEach(function(d){var k=d.dataset.k; if(k in st) d.open=st[k];});
  ds.forEach(function(d){d.addEventListener('toggle',function(){
    st[d.dataset.k]=d.open; try{localStorage.setItem(KEY,JSON.stringify(st));}catch(e){}});});
  function all(v){ds.forEach(function(d){d.open=v;});}
  document.getElementById('openall').onclick=function(){all(true);};
  document.getElementById('closeall').onclick=function(){all(false);};
  document.getElementById('reset').onclick=function(){
    try{localStorage.removeItem(KEY);}catch(e){} sessionStorage.removeItem('daily-scroll'); location.reload();};
  // リロード跨ぎでスクロール位置を保つ
  try{var y=sessionStorage.getItem('daily-scroll'); if(y) window.scrollTo(0, +y);}catch(e){}
  // 変更検知: daily_stamp.js を差し込み直して STAMP を比べ、違えばリロード
  window.__dailyStamp=function(s){
    if(s && s!==STAMP){
      try{sessionStorage.setItem('daily-scroll', String(window.scrollY));}catch(e){}
      location.reload();
    }
  };
  setInterval(function(){
    var sc=document.createElement('script');
    sc.src='daily_stamp.js?t='+Date.now();
    sc.onload=sc.onerror=function(){sc.remove();};
    document.head.appendChild(sc);
  }, POLL_MS);
})();
"""


# 見た目のコードを変えたら stamp も変わる＝開いているタブが新しい見た目に追従する
_CODE_VER = hashlib.sha1((CSS + PAGE_JS).encode()).hexdigest()[:8]


# ---------------------------------------------------------------- 変換（純関数）

def _link_resolver(base_dir: Path):
    """daily ディレクトリ基準の相対リンク → file:/// URI。ページ内アンカーは触らない。"""
    def resolve(href: str):
        if not href or href.startswith("#"):
            return None  # ページ内アンカー
        if re.match(r"^[a-z][a-z0-9+.-]+:", href, re.I):
            return None  # mailto: 等のスキーム付き（2文字以上＝ドライブレター C: は除外）
        h = unquote(href.split("#", 1)[0])
        frag = "#" + href.split("#", 1)[1] if "#" in href else ""
        p = Path(h) if re.match(r"^[a-z]:[\\/]", h, re.I) or h.startswith(("\\\\", "/")) \
            else (base_dir / h)
        try:
            return p.resolve().as_uri() + frag
        except ValueError:
            return None
    return resolve


def _parse(md: str):
    """見出しツリーに分解する。コードブロック内・引用内の # は見出しとみなさない。
    返り値: (h1タイトル or None, 前文 lines, [node])  node = {lv, title, lines, kids}"""
    h1, pre, top, stack = None, [], [], []
    fence = False
    for ln in md.splitlines():
        if ln.strip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,4})\s+(.*?)\s*#*\s*$", ln)
        if m and len(m.group(1)) == 1 and h1 is None and not top:
            h1 = m.group(2)
            continue
        if m and len(m.group(1)) >= 2:
            node = {"lv": len(m.group(1)), "title": m.group(2), "lines": [], "kids": []}
            while stack and stack[-1]["lv"] >= node["lv"]:
                stack.pop()
            (stack[-1]["kids"] if stack else top).append(node)
            stack.append(node)
            continue
        (stack[-1]["lines"] if stack else pre).append(ln)
    return h1, pre, top


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


def _matches(path_titles, key: str) -> bool:
    """key="A > B" → 末尾が B を含み、祖先のどれかが A を含む。key="B" → 末尾が B を含む。"""
    parts = [_norm(p) for p in key.split(">") if p.strip()]
    if not parts or parts[-1] not in _norm(path_titles[-1]):
        return False
    anc, j = [_norm(t) for t in path_titles[:-1]], 0
    for p in parts[:-1]:
        while j < len(anc) and p not in anc[j]:
            j += 1
        if j >= len(anc):
            return False
        j += 1
    return True


def _todo_count(node) -> tuple:
    """セクション配下（子孫込み）の ⬜ / 🔄 の個数＝閉じていても残りが見える。"""
    text = "\n".join(node["lines"])
    todo, doing = text.count("⬜"), text.count("🔄")
    for k in node["kids"]:
        a, b = _todo_count(k)
        todo, doing = todo + a, doing + b
    return todo, doing


def render_daily(md: str, base_dir: Path, open_keys=None):
    """daily の markdown → (h1 タイトル, 本文 HTML)。I/O なし（段階2のサーバからも呼べる）。"""
    open_keys = DEFAULT_OPEN if open_keys is None else open_keys
    link = _link_resolver(base_dir)
    h1, pre, top = _parse(md)

    def node_html(node, path, forced):
        titles = path + [node["title"]]
        hit = forced or any(_matches(titles, k) for k in open_keys)
        inner = md_render("\n".join(node["lines"]), link)
        kids = [node_html(k, titles, hit) for k in node["kids"]]
        is_open = hit or any(o for _, o in kids)  # 開く子孫がいれば祖先も開く
        todo, doing = _todo_count(node)
        cnt = "".join([f"⬜{todo}" if todo else "", f" 🔄{doing}" if doing else ""]).strip()
        cnt_html = f'<span class="cnt">{cnt}</span>' if cnt else ""
        key = html_mod.escape(" > ".join(titles), quote=True)
        lv = min(node["lv"], 4)
        h = (f'<details class="l{lv}" data-k="{key}"{" open" if is_open else ""}>'
             f'<summary>{_inline(node["title"], link)}{cnt_html}</summary>'
             f'<div class="body">{inner}{"".join(x for x, _ in kids)}</div></details>')
        return h, is_open

    body = md_render("\n".join(pre), link) + "".join(node_html(n, [], False)[0] for n in top)
    return h1, body


# ---------------------------------------------------------------- 書き出し（I/O）

def _page(title, sub_html, body_html, stamp, day, poll_ms):
    js = (f"var STAMP={json.dumps(stamp)}, DAY={json.dumps(day)}, POLL_MS={poll_ms};"
          + PAGE_JS)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📓 業務日誌</title>
<script>{HEAD_JS}</script>
<style>{CSS}</style></head>
<body><div class="wrap">
<header><div><h1>{html_mod.escape(title)}</h1>
<div class="sub">いま <b id="clock"></b> ／ {sub_html}</div></div>
<div class="navs">{nav_html("daily")}<button class="tgl" id="tgl"></button></div></header>
<div class="bar"><button class="tgl" id="openall">すべて開く</button>
<button class="tgl" id="closeall">すべて閉じる</button>
<button class="tgl" id="reset" title="手動の開閉を忘れて既定に戻す">既定の開き方に戻す</button></div>
{body_html}
<footer>live_board.py --html --watch が daily を監視して書き換え中。変更は数秒〜10秒で反映（タブは開きっぱなしでOK）。</footer>
</div><script>{js}</script></body></html>"""


def write_daily(cfg: dict, out_dir: Path, today: date = None, poll_ms: int = 5000) -> bool:
    """今日の daily を out_dir/daily.html に書く。中身が変わった時だけ書いて True。
    cfg["daily_view"] が無ければ何もしない（汎用配布で daily を持たない人向け）。"""
    dv = cfg.get("daily_view") or {}
    if not dv.get("dir"):
        return False
    ddir = Path(dv["dir"]).expanduser()
    today = today or date.today()
    day = today.isoformat()
    src = ddir / f"{day}.md"
    label = f"{today.month}/{today.day}（{WEEKDAY[today.weekday()]}）"

    if src.exists():
        raw = src.read_bytes()
        md = raw.decode("utf-8", errors="replace")
        mtime = datetime.fromtimestamp(src.stat().st_mtime)
        stamp = hashlib.sha1(day.encode() + raw + json.dumps(dv, ensure_ascii=False).encode()
                             + _CODE_VER.encode()).hexdigest()[:16]
        h1, body = render_daily(md, ddir, dv.get("open"))
        title = h1 or f"{day} 業務日誌"
        sub = (f'最終更新 <b>{mtime:%m/%d %H:%M:%S}</b> ／ '
               f'<a href="{src.resolve().as_uri()}">{html_mod.escape(src.name)}</a>')
    else:
        stamp = "missing:" + day
        prev = sorted(p for p in ddir.glob("20??-??-??.md") if p.name < src.name)[-1:]
        prev_html = (f'直近の日誌: <a href="{prev[0].resolve().as_uri()}">{prev[0].name}</a>'
                     if prev else "")
        title = f"{label} 業務日誌"
        sub = "最終更新 <b>—</b>"
        body = (f'<div class="missing">今日（{label}）の日誌はまだ作成されていません。'
                f'業務ハブを起動すると作られ、このタブに自動で出ます。<br>{prev_html}</div>')

    stamp_path = out_dir / "daily_stamp.js"
    stamp_js = f"window.__dailyStamp&&window.__dailyStamp({json.dumps(stamp)});"
    out_path = out_dir / "daily.html"
    try:
        if out_path.exists() and stamp_path.read_text(encoding="utf-8") == stamp_js:
            return False
    except OSError:
        pass
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_page(title, sub, body, stamp, day, poll_ms), encoding="utf-8")
    stamp_path.write_text(stamp_js, encoding="utf-8")  # 本体の後に書く＝読み込み時は必ず新しい本体
    return True


# ---------------------------------------------------------------- 🧾 ホワイトボード用の要点（S23）
# office.html の休憩室ホワイトボードに書く「今日の daily の要点」。機械的に取れる分だけ（ハブ提案 10/15）:
#   🔴 TOP の見出し（本日の予定 > TOP の h4・番号と済みの別）／⏰ 今日の縛り（時刻つきの予定だけ）／返信待ちの件数と相手

_MARK_RX = re.compile(r"^([\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u23E9-\u23FA]\uFE0F?)\s*")
_TIME_RX = re.compile(
    r"(\d{1,2})[:：](\d{2})(?:\s*[〜~～\-]\s*(\d{1,2})[:：](\d{2}))?(?:\s*[〜~～])?(?:前後|頃|ごろ|過ぎ)?"
    r"|(\d{1,2})時(?:(\d{1,2})分|(半))?(?:前後|頃|ごろ|過ぎ)?")


def _plain(s: str) -> str:
    """markdown の飾りを落として1行に（リンク→文字・**/__/~~/` を除去）。"""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*|__|~~|`", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _find(nodes, key, path=()):
    """_matches 形式の key（"本日の予定 > TOP"）に最初に合う見出しノード（文書順）。"""
    for n in nodes:
        titles = list(path) + [n["title"]]
        if _matches(titles, key):
            return n
        hit = _find(n["kids"], key, titles)
        if hit:
            return hit
    return None


def _top_items(top) -> list:
    node = _find(top, "本日の予定 > TOP") or _find(top, "TOP")
    if not node:
        return []
    heads = [k["title"] for k in node["kids"]]
    if not heads:  # 見出しでなく箇条書きで書かれている日
        heads = [ln[2:] for ln in node["lines"] if ln.startswith("- ")]
    out = []
    for h in heads:
        done = "~~" in h  # 打ち消し線＝済み
        t = re.sub(r"^\d+[.．)]\s*", "", _plain(h))
        m = _MARK_RX.match(t)
        mark = m.group(1) if m else ""
        if m:
            t = t[m.end():]
        out.append({"t": t, "mark": mark, "done": done or mark.startswith("✅")})
    return out


def _events(md: str) -> list:
    """「⏰ 今日の縛り: 10:00 朝会／12:00〜 ロジクラ連携／…」→ [{hm, to, label}]（時刻順）。時刻の無い項目は落とす。"""
    m = re.search(r"⏰\s*今日の縛り\s*[:：]\s*(.+)", md) or re.search(r"^\s*-\s*⏰\s*(.+)", md, re.M)
    if not m:
        return []
    out = []
    for item in re.split(r"\s*／\s*", _plain(m.group(1))):
        tm = _TIME_RX.search(item)
        if not tm:
            continue
        if tm.group(1):
            hm = f"{int(tm.group(1)):02d}:{tm.group(2)}"
            to = f"{int(tm.group(3)):02d}:{tm.group(4)}" if tm.group(3) else None
        else:
            mi = int(tm.group(6)) if tm.group(6) else (30 if tm.group(7) else 0)
            hm, to = f"{int(tm.group(5)):02d}:{mi:02d}", None
        label = (item[:tm.start()] + " " + item[tm.end():]).strip()
        label = re.sub(r"[（(]\s*[)）]", "", label).strip(" 〜~～-・:：") or "予定"
        out.append({"hm": hm, "to": to, "label": label})
    out.sort(key=lambda e: e["hm"])
    return out


def _waiting(top):
    """「返信待ち」見出し直下の箇条書き＝1件。✅ 始まりは解消済みで除外・🔴 始まりは先頭に。相手は【…】を採る。"""
    node = _find(top, "返信待ち")
    if not node:
        return None
    rows = []
    for ln in node["lines"]:
        if not ln.startswith("- "):
            continue  # 入れ子（補足）は数えない
        body = ln[2:].strip()
        if body.startswith("✅"):
            continue
        urgent = body.startswith("🔴")
        m = re.search(r"【([^】]+)】", body)
        rows.append((0 if urgent else 1, _plain(m.group(1)) if m else _plain(body)[:14]))
    rows.sort(key=lambda r: r[0])
    return {"n": len(rows), "urgent": sum(1 for r in rows if r[0] == 0), "items": [r[1] for r in rows]}


def board_summary(md: str) -> dict:
    """🧾 ホワイトボードに書く要点（I/O なし）。{"top": [{t, mark, done}], "events": [{hm, to, label}], "waiting": {n, urgent, items} | None}"""
    _, _, top = _parse(md)
    return {"top": _top_items(top), "events": _events(md), "waiting": _waiting(top)}


def load_board(cfg: dict, today: date = None):
    """今日の daily → board_summary。daily_view 未設定なら None（＝板は S22 の小さいまま）・日誌が無ければ {"missing": True}。"""
    dv = cfg.get("daily_view") or {}
    if not dv.get("dir"):
        return None
    today = today or date.today()
    src = Path(dv["dir"]).expanduser() / f"{today.isoformat()}.md"
    if not src.exists():
        return {"day": today.isoformat(), "missing": True}
    b = board_summary(src.read_text(encoding="utf-8", errors="replace"))
    b["day"] = today.isoformat()
    return b


if __name__ == "__main__":  # 単発生成: python -m claude_sv.daily_view
    import sys
    from claude_sv.mapping import load_config
    repo = Path(__file__).resolve().parent.parent
    sys.stdout.reconfigure(encoding="utf-8")
    print("written" if write_daily(load_config(repo), repo / "reports") else "unchanged/skip")
