"""バーチャルオフィス配置図（reports/office.html・S21 段階②）。

席＝トラック（ProjectMapper の案件名）・人＝稼働中セッション（シャツ色＝モデル）・矢印＝SendMessage。
中央の大きい席が業務ハブ。データは messages.collect（送受信の突合）と live_board の rows（稼働状態）だけ
＝新しい記録場所は作らない。ローカル閲覧専用（案件名・要約が入るので公開しない）。

S22（10/15）: ビル＝テナント（部屋）＝ワークスペース単位。cfg.office.tenants {部屋名: [encoded ws dir か
workspace_labels の表示名]} の最初の部屋が本業（ハブと島わけ=teams はここ）・残りは部屋ごとに席を格子で並べる・
どの部屋にも無い ws は other_tenant（既定「その他」）。?ws=〈部屋名の一部〉／ヘッダのボタンで1部屋だけに絞れる
（隠した部屋宛の矢印は右端のドアへ出る＝壁の外へ出る線）。
🧲 ハブ寄せ（S22）: cfg.office.live_near_hub=true で稼働中の席を島の中でハブに近いマスから詰める（休眠席は残りに
config の順）。ヘッダの 🧲 ボタンで切替（localStorage）。席は状態で動く＝同じトラックが毎回同じマスとは限らない。

更新の仕組みは daily_view と同じ: 中身が変わった時だけ office.html を書き、ページは office_stamp.js を
数秒おきに読んで差分があればリロードする（スクロール・選択は保たない＝配置図なので不要）。
"""
import base64
import hashlib
import html as html_mod
import json
import re
from datetime import datetime
from pathlib import Path

from .messages import hub_names
from .nav import nav_html

STATUS_MAP = {"working": "busy", "waiting_input": "idle", "waiting_permission": "perm"}


def _fmt_local(ts_iso: str) -> str:
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone().strftime("%m/%d %H:%M")
    except (AttributeError, ValueError):
        return "?"


SPRITE_DIR = Path(__file__).resolve().parent.parent / "assets" / "sprites"
SPRITE_NAMES = ("human", "agent", "agent-opus", "agent-sonnet", "agent-haiku", "agent-fable")


def load_sprites(sprite_dir: Path = SPRITE_DIR) -> dict:
    """assets/sprites/<name>.png|svg を data URI にして返す（無ければ空＝コード内ピクセル絵）。仕様=同フォルダ README。"""
    out = {}
    for name in SPRITE_NAMES:
        for ext, mime in (("png", "image/png"), ("svg", "image/svg+xml")):
            f = sprite_dir / f"{name}.{ext}"
            if f.exists():
                try:
                    out[name] = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()
                except OSError:
                    pass
                break
    return out


def _norm(name: str) -> str:
    """島わけ突合用の正規化: 空白・大小無視、末尾の「（仮）」等の括弧注記は落とす（registry の正式名と mapper の案件名のズレ吸収）。"""
    n = re.sub(r"\s+", "", name or "")
    n = re.sub(r"[（(][^（()）]*[)）]$", "", n) or n  # 「（未分類）」のように全部が括弧なら残す
    return n.casefold()


def _tenants(cfg: dict):
    """cfg.office.tenants（部屋名 → [encoded ws dir か workspace_labels の表示名]）を突合表にする。無ければ None＝部屋なし。
    最初の部屋＝本業（ハブ・teams の島はここ）。どの部屋にも無い ws は other_tenant（既定「その他」）。"""
    office = cfg.get("office") or {}
    tenants = office.get("tenants") or {}
    if not tenants:
        return None
    idx = {}
    for name, keys in tenants.items():
        for k in (keys if isinstance(keys, list) else [keys]):
            idx[str(k).casefold()] = name
    names = list(tenants)
    return {"names": names, "main": names[0], "other": office.get("other_tenant", "その他"), "idx": idx}


def build_islands(order, cfg: dict) -> list:
    """cfg.office.teams（島名 → トラック名リスト・順序＝配置の優先）から島を組む。無ければ [] ＝従来の楕円配置。

    トラック名は ProjectMapper の案件名（＝alias の正規名）と空白・大小無視で突合。どの島にも無い
    トラックは「その他」島。cfg.office.island_slots で島の置き場を指定できる（r/l/t/b/tr/tl/br/bl）。
    """
    office = cfg.get("office") or {}
    teams = office.get("teams") or {}
    if not teams:
        return []
    slots = office.get("island_slots") or {}
    names = [t["name"] for t in order]
    by_norm = {_norm(n): n for n in names}
    rank = {n: i for i, n in enumerate(names)}
    assigned: set = set()
    islands = []
    for isl, members in teams.items():
        picked = []
        for m in members or []:
            k = _norm(m)
            if k in by_norm and k not in assigned:
                picked.append(by_norm[k])
                assigned.add(k)
        if picked:
            picked.sort(key=lambda n: rank[n])
            islands.append({"name": isl, "tracks": picked, "slot": slots.get(isl)})
    rest = [n for n in names if _norm(n) not in assigned]
    if rest:
        other = office.get("other_island", "その他")
        islands.append({"name": other, "tracks": rest, "slot": slots.get(other)})
    return islands


def load_sprite_themes(sprite_dir: Path = SPRITE_DIR) -> dict:
    """assets/sprites/<テーマ>/ ごとの絵を {テーマ名: {name: data URI}} で返す（人間の候補 human-2.png… も含む）。
    ページのプルダウンで切り替える用。samples/（作業用）は除外。"""
    out = {}
    if not sprite_dir.is_dir():
        return out
    for d in sorted(sprite_dir.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")) or d.name == "samples":
            continue
        # テーマ内は human*.png / agent*.png を全部拾う（sheet.png 等は除外）。命名＝README「種類・モデル・ポーズ」
        sp = {}
        for f in sorted(d.iterdir()):
            if f.suffix not in (".png", ".svg") or not f.stem.startswith(("human", "agent")):
                continue
            if f.suffix == ".svg" and f.stem in sp:
                continue  # png 優先
            mime = "image/png" if f.suffix == ".png" else "image/svg+xml"
            try:
                sp[f.stem] = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()
            except OSError:
                pass
        if sp:
            out[d.name] = sp
    return out


def build_office_data(res: dict, labels: dict, rows, cfg: dict) -> dict:
    """messages.collect の結果＋ラベル＋稼働 rows から、描画用の D を組む（JS 側は配置と描画だけ）。"""
    hub_disp = (cfg.get("office") or {}).get("hub", "業務ハブ")
    hubs = hub_names(cfg)
    registry = res.get("registry") or {}
    tc = _tenants(cfg)  # 部屋（テナント）設定・無ければ None

    fold: dict = {}  # 大文字小文字・空白だけ違う案件名は同じ席に寄せる（alias 未登録の案件の保険）

    def proj(sid):
        lb = labels.get(sid) or {}
        p = lb.get("project")
        if not p or p == "—":
            return None
        if p in hubs:
            return hub_disp
        return fold.setdefault(re.sub(r"\s+", "", p).casefold(), p)

    live = {}
    for r in rows or []:
        sid = r.get("sid")
        if not sid:
            continue
        live[sid] = {"status": STATUS_MAP.get(r.get("state"), "idle"),
                     "name": (registry.get(sid) or {}).get("name") or "",
                     "named": (registry.get(sid) or {}).get("nameSource") == "user",  # /rename 済み（自動命名でない）
                     "model": r.get("model") or "", "title": (r.get("title") or "")[:60],
                     "last_human": r.get("last_human"),
                     "ws": r.get("ws") or "", "ws_key": r.get("ws_key") or "", "cwd": r.get("cwd") or ""}
        if sid not in labels and r.get("project") and r["project"] != "—":
            labels[sid] = {"project": r["project"], "s": r.get("session_no"),
                           "ws": r.get("ws") or "", "ws_key": r.get("ws_key") or ""}

    def tenant_of(sid):
        """セッションの部屋: encoded ws dir → 表示名 → cwd 末尾フォルダ名 の順で cfg.office.tenants と突合。無ければ other。"""
        if not tc:
            return None
        lb, lv = labels.get(sid) or {}, live.get(sid) or {}
        for c in (lb.get("ws_key"), lv.get("ws_key"), lb.get("ws"), lv.get("ws"), Path(lv.get("cwd") or "").name):
            if c and str(c).casefold() in tc["idx"]:
                return tc["idx"][str(c).casefold()]
        return tc["other"]

    def seat(sid):
        """席名: 案件名 → /rename 済みの名前（10/15 S21 見直し③・例「絵の作成」）→ 未分類。
        本業以外の部屋の未分類席はワークスペース名で出す（部屋をまたいで「（未分類）」1席に溶けないため）。"""
        p = proj(sid)
        if p:
            return p
        lv = live.get(sid) or {}
        if lv.get("named") and lv.get("name"):
            return lv["name"]
        if tc and tenant_of(sid) != tc["main"]:
            w = (labels.get(sid) or {}).get("ws") or lv.get("ws")
            if w:
                return f"（{w}）"
        return "（未分類）"

    def seat_or_none(sid):
        return seat(sid) if (sid in labels or sid in live) else None

    tracks: dict = {}
    for sid in set(labels) | set(live):
        p = seat(sid)
        t = tracks.setdefault(p, {"name": p, "sessions": []})
        t["sessions"].append({"sid": sid, "s": (labels.get(sid) or {}).get("s"),
                              "live": live.get(sid)})
    for t in tracks.values():
        t["sessions"].sort(key=lambda x: (x["live"] is None, -(x["s"] or 0)))
        t["occupied_by"] = [x for x in t["sessions"] if x["live"]]
        t["live"] = bool(t["occupied_by"])
        t["cur"] = t["sessions"][0] if t["sessions"] else None
        # 席の部屋＝いちばん新しい（稼働優先）セッションの部屋。同じ案件を別 ws で開いても席は1つ
        t["tenant"] = tenant_of(t["cur"]["sid"]) if (tc and t["cur"]) else (tc["main"] if tc else None)
    hub = tracks.pop(hub_disp, {"name": hub_disp, "sessions": [], "occupied_by": [],
                                "live": False, "cur": None})
    order = sorted(tracks.values(), key=lambda t: (not t["live"], t["name"]))

    # 止まっているトラックも空席で出す（10/15 ふくちゃん）: cfg.office.teams に載っている名前のうち
    # 時間幅内にセッションが無いものを sessions=[] の席として足す（島わけで席が固定され、空席＝止まって
    # いるトラックが見える）。既定 on・office.show_idle_tracks=false で従来どおり稼働分だけ。
    office_cfg = cfg.get("office") or {}
    if office_cfg.get("show_idle_tracks", True):
        have = {_norm(t["name"]) for t in order} | {_norm(hub_disp)}
        for members in (office_cfg.get("teams") or {}).values():
            for m in members or []:
                if _norm(m) in have:
                    continue
                have.add(_norm(m))
                order.append({"name": m, "sessions": [], "occupied_by": [], "live": False,
                              "cur": None, "dormant": True, "tenant": tc["main"] if tc else None})

    # 人間の位置＝稼働セッションのうち「人間の最終発話」が最新の1件の席の横（S21・ハブ案）。
    # 複数ウィンドウで同時に打っていても最新1件で割り切る。注意の所在の指標なので、話しかけて
    # いないのに busy な席＝勝手に進んでいるエージェントが浮き上がる。表示名は cfg.office.human。
    human = None
    for t in [hub] + order:
        for x in t["occupied_by"]:
            lh = (x["live"] or {}).get("last_human")
            if lh and (human is None or lh["ts"] > human["ts"]):
                human = {"track": t["name"], "sid": x["sid"], "s": x["s"], "ts": lh["ts"],
                         "t": datetime.fromtimestamp(lh["ts"]).strftime("%H:%M"), "text": lh["text"]}
    if human:
        human["name"] = (cfg.get("office") or {}).get("human", "人間")

    edges = []
    for e in res.get("edges") or []:
        fp, tp = seat_or_none(e["frm"]), seat_or_none(e["to"])
        edges.append({
            "ts": e["ts"], "t": _fmt_local(e["ts"]), "day": _fmt_local(e["ts"])[:5],
            "frm": fp or "（未分類）", "to": tp or (e.get("to_label") or "？"),
            "frm_s": (labels.get(e["frm"]) or {}).get("s"), "to_s": (labels.get(e["to"]) or {}).get("s"),
            "to_known": tp is not None,
            "summary": e.get("summary") or e.get("preview") or "", "preview": e.get("preview") or "",
            "ok": e.get("ok", True), "read": e.get("read"), "removed": bool(e.get("removed")),
        })
    unread = sum(1 for e in edges if e["ok"] and not e["removed"] and e["read"] is False)
    failed = sum(1 for e in edges if not e["ok"])

    # 部屋（S22）: 最初の部屋＝本業（ハブ＋島わけ）・残りは席の一覧。設定済みの部屋は空室でも出す・
    # other（どこにも無い ws）は席がある時だけ出す。部屋が未設定なら従来どおり islands だけ
    rooms = []
    islands = build_islands(order, cfg)
    if tc:
        islands = build_islands([t for t in order if t["tenant"] == tc["main"]], cfg)
        rooms.append({"name": tc["main"], "main": True, "islands": islands})
        for n in tc["names"][1:] + [tc["other"]]:
            ts = [t["name"] for t in order if t["tenant"] == n]
            if ts or n != tc["other"]:
                rooms.append({"name": n, "main": False, "tracks": ts})
    return {"hub": hub, "tracks": order, "edges": edges, "human": human, "sprites": load_sprites(), "sprite_themes": load_sprite_themes(),
            "sprite_default": (cfg.get("office") or {}).get("sprite_theme", ""),  # 既定テーマ（プルダウン未選択時）
            "sprite_variants": (cfg.get("office") or {}).get("sprite_variants") or {},  # トラック名→種類名の固定割当（任意）
            "sprite_track_themes": (cfg.get("office") or {}).get("sprite_track_themes") or {},  # トラック名→テーマ名（席ごとの別テーマ・例: 業務ハブだけ ninja）
            "islands": islands, "rooms": rooms,
            "live_near_hub": bool((cfg.get("office") or {}).get("live_near_hub", False)),  # 🧲 稼働席をハブ側に詰める既定（S22）
            "counts": {"edges": len(edges), "unread": unread, "failed": failed,
                       "live": sum(len(t["occupied_by"]) for t in order) + len(hub["occupied_by"])},
            "window_hours": (cfg.get("office") or {}).get("window_hours", 48)}


CSS = """
*{box-sizing:border-box}
:root{color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
  --border:rgba(255,255,255,.10);--wash:rgba(255,255,255,.055);--gold:#f5c542;--cyan:#5ad1e6;--mag:#e66fd1;--red:#e5533b}
:root[data-theme="light"]{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --border:rgba(11,11,11,.10);--wash:rgba(11,11,11,.045);--gold:#b8860b;--cyan:#1d8fa6;--mag:#b3399e;--red:#c2341f}
html,body{height:100%} body{margin:0;height:100vh;overflow:hidden;display:flex;flex-direction:column;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;font-size:13px;line-height:1.5}
header{flex:none;padding:10px 16px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:600} .sub{color:var(--muted);font-size:11.5px}
.navs{margin-left:auto;display:flex;gap:6px;align-items:center}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;padding:3px 11px;font-size:12px;cursor:pointer;text-decoration:none;font-family:inherit}
.tgl:hover{background:var(--wash);color:var(--ink)} .tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
.wsb{padding:1px 8px;font-size:11px;margin-left:3px} .tgl.on{background:var(--wash);color:var(--ink);border-color:var(--ink2)}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;grid-template-rows:minmax(0,1fr);flex:1 1 0;min-height:0;overflow:hidden}
#scene{overflow:hidden;background:#1b1d2a;min-height:0;height:100%} #scene svg{display:block;width:100%;height:100%;shape-rendering:crispEdges}
aside{background:var(--surface);border-left:1px solid var(--border);overflow-y:auto;min-height:0;height:100%;padding:10px 12px}
aside h2{position:sticky;top:-10px;background:var(--surface);padding:6px 0;z-index:1}
aside h2{font-size:12.5px;margin:4px 0 8px;color:var(--muted);font-weight:600;display:flex;align-items:center;gap:8px} aside h2 .tgl{margin-left:auto;padding:1px 8px;font-size:11px}
#spr{font:inherit;font-size:11px;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:1px 4px}
.day{color:var(--muted);font-size:11px;margin:10px 0 2px;border-bottom:1px solid var(--border)}
.row{padding:5px 6px;border-left:3px solid transparent;cursor:pointer;line-height:1.35;border-radius:0 6px 6px 0}
.row:hover,.row.hl{background:var(--wash);border-left-color:var(--gold)}
.row .t{color:var(--muted);margin-right:6px;font-variant-numeric:tabular-nums}.row .who{color:var(--cyan)}
.row .un{color:var(--gold);font-weight:600}.row .ng{color:var(--red);font-weight:600}.row .rm{color:var(--muted)}
.row .sm{color:var(--ink2)}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--ink2)}
.sw{display:inline-block;width:10px;height:10px;margin-right:4px;vertical-align:middle;border-radius:2px}
.tiles{display:flex;gap:8px} .tile{border:1px solid var(--border);border-radius:8px;padding:2px 10px;font-size:12px;color:var(--ink2)}
.tile b{color:var(--ink);font-size:14px;margin-right:3px} .tile.warn b{color:var(--gold)} .tile.bad b{color:var(--red)}
.edge{fill:none;stroke-width:3;cursor:pointer} .edge.hl{stroke-width:5;filter:drop-shadow(0 0 4px #fff)}
.edge.ng{stroke-dasharray:6 5} .edge.rm{stroke-dasharray:2 4}
@keyframes blink{50%{opacity:0}} .cur{animation:blink 1s step-end infinite}
@keyframes type{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}} .typing{animation:type .5s steps(2) infinite}
@keyframes alert{50%{opacity:.35}} .perm{animation:alert .8s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}} .human{animation:bob 1.6s ease-in-out infinite}
.empty{padding:30px;color:var(--muted)}
footer{flex:none;padding:6px 16px;color:var(--muted);font-size:11px;border-top:1px solid var(--border)}
"""

HEAD_JS = "try{var t=localStorage.getItem('csv-theme');if(t)document.documentElement.dataset.theme=t;}catch(e){}"

# D / STAMP / POLL_MS / HUB はサーバ側で埋める
PAGE_JS = r"""
(function(){
var root=document.documentElement, btn=document.getElementById('tgl');
function lab(){btn.textContent=root.dataset.theme==='light'?'☾ ダーク':'☀ ライト';} lab();
btn.onclick=function(){root.dataset.theme=root.dataset.theme==='light'?'dark':'light';try{localStorage.setItem('csv-theme',root.dataset.theme);}catch(e){} lab();};
var clk=document.getElementById('clock'), WD='日月火水木金土';
function tick(){var d=new Date(),p=function(n){return('0'+n).slice(-2);};clk.textContent=(d.getMonth()+1)+'/'+d.getDate()+'（'+WD[d.getDay()]+'） '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}
tick(); setInterval(tick,1000);
window.__officeStamp=function(s){if(s&&s!==STAMP)location.reload();};
setInterval(function(){var sc=document.createElement('script');sc.src='office_stamp.js?t='+Date.now();sc.onload=sc.onerror=function(){sc.remove();};document.head.appendChild(sc);},POLL_MS);

// ---------- 配置図 ----------
var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
var n=D.tracks.length, RX=Math.max(430, n*62), RY=270, W=RX*2+260, H=740, CX=W/2, CY=380;
var MODEL_COL=function(m){return !m?'#c24b4b':/opus/.test(m)?'#8a5cf6':/sonnet/.test(m)?'#3b82f6':/haiku/.test(m)?'#d4a017':/fable/.test(m)?'#f472b6':'#c24b4b';};
// エージェント＝ロボット（アンテナ・金属の頭・光る目）。シャツ色＝モデル。人間（下の HS）とは頭の形で見分ける
var SPR=["....AA......","....AA......","..MMMMMMMM..",".MMMMMMMMMM.",".MVVMMMMVVM.",".MMMMMMMMMM.","..MMMMMMMM..","....MM......","..TTTTTTTT..",".TTTTTTTTTT.",".M.TTTTTT.M.",".M.TTTTTT.M.","...TTTTTT...","...MM..MM...","...MM..MM...","...BB..BB..."];
var PAL={M:'#b8c0cc',V:'#5ad1e6',B:'#222'};
// 絵のテーマ: assets/sprites/ 直下（既定）か assets/sprites/<テーマ>/。選択は localStorage に残し、切替はページ再読込で反映
var THEMES=D.sprite_themes||{}, SP=D.sprites||{}, tints={}, sprSel='';
try{sprSel=localStorage.getItem('csv-sprite-theme')||'';}catch(e){}
try{var sprQ=new URLSearchParams(location.search).get('spr');if(sprQ!==null)sprSel=sprQ;}catch(e){}  // ?spr=ninja で一時指定（スクショ・共有用）
(function(){var sel=document.getElementById('spr');if(!sel)return;var E=function(v){return String(v).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};var defT=D.sprite_default||'';var opts=[['','既定'+(defT&&THEMES[defT.split('/')[0]]?'＝'+defT:'（直下の絵／無ければコード内）')]];
 Object.keys(THEMES).forEach(function(t){opts.push([t,t]);Object.keys(THEMES[t]).filter(function(k){return /^human-\d+$/.test(k);}).sort().forEach(function(k){opts.push([t+'/'+k,t+'（人間'+k.slice(6)+'）']);});});
 if(sprSel&&!opts.some(function(o){return o[0]===sprSel;}))sprSel='';
 sel.innerHTML=opts.map(function(o){return '<option value="'+E(o[0])+'"'+(o[0]===sprSel?' selected':'')+'>'+E(o[1])+'</option>';}).join('');
 sel.onchange=function(){try{localStorage.setItem('csv-sprite-theme',sel.value);}catch(e){}location.reload();};
 var eff=sprSel||defT; if(eff){var parts=eff.split('/'),th=THEMES[parts[0]];if(th){SP={};Object.keys(th).forEach(function(k){SP[k]=th[k];});if(parts[1]&&th[parts[1]])SP.human=th[parts[1]];}}})();
function img(href,x,y,filt){return '<image href="'+href+'" x="'+x+'" y="'+y+'" width="36" height="48" preserveAspectRatio="xMidYMax meet" style="image-rendering:pixelated"'+(filt?' filter="url(#'+filt+')"':'')+'/>';}
// テーマ内の「種類」＝ agent-<種類>[-<モデル>][-<ポーズ>].png の <種類>（モデル名・ポーズ名以外のトークン）。トラック名のハッシュで固定割当（config office.sprite_variants で上書き可）
var MODELS={opus:1,sonnet:1,haiku:1,fable:1,unknown:1}, POSES={typing:1,idle:1,wait:1};
function varsOf(M){if(M.__vars)return M.__vars;var vs={};Object.keys(M).forEach(function(k){var t=k.split('-');if(t[0]!=='agent')return;t.slice(1).forEach(function(w){if(!MODELS[w]&&!POSES[w])vs[w]=1;});});return (M.__vars=Object.keys(vs).sort());}
var VOVR=D.sprite_variants||{}, TOVR=D.sprite_track_themes||{};
function variantFor(track,M){var VARS=varsOf(M||SP);if(!VARS.length)return '';if(VOVR[track]&&VARS.indexOf(VOVR[track])>=0)return VOVR[track];var h=5381;for(var i=0;i<track.length;i++)h=((h*33)^track.charCodeAt(i))>>>0;return VARS[h%VARS.length];}
// 席ごとの別テーマ（config office.sprite_track_themes {"トラック名": "テーマ"}）。テーマが無ければ画面のテーマに落ちる
function mapFor(track){var th=TOVR[track];if(th){var tm=THEMES[String(th).split('/')[0]];if(tm)return tm;}return SP;}
function modelKey(model){return /opus/.test(model||'')?'opus':/sonnet/.test(model||'')?'sonnet':/haiku/.test(model||'')?'haiku':/fable/.test(model||'')?'fable':'unknown';}
function sprite(x,y,shirt,model,status,track){var M=mapFor(track||''),mk=modelKey(model),pose=status==='busy'?'typing':status==='perm'?'wait':'idle',v=variantFor(track||'',M);
 var direct=[],gray=[];
 if(v){direct.push('agent-'+v+'-'+mk+'-'+pose,'agent-'+v+'-'+mk);gray.push('agent-'+v+'-'+pose,'agent-'+v);}
 direct.push('agent-'+mk+'-'+pose,'agent-'+mk);gray.push('agent-'+pose,'agent');
 for(var i=0;i<direct.length;i++)if(M[direct[i]])return img(M[direct[i]],x,y,null);
 for(var j=0;j<gray.length;j++)if(M[gray[j]]){var id='tint'+shirt.replace('#','');tints[id]=shirt;return img(M[gray[j]],x,y,id);}
 var s='',sc=3;SPR.forEach(function(row,r){row.split('').forEach(function(c,i){if(c==='.')return;var col=(c==='T'||c==='A')?shirt:PAL[c];s+='<rect x="'+(x+i*sc)+'" y="'+(y+r*sc)+'" width="'+sc+'" height="'+sc+'" fill="'+col+'"/>';});});return s;}
var SCR={busy:'#39d353',idle:'#4aa3ff',perm:'#e5533b',empty:'#555'};
var unreadBy={}; D.edges.forEach(function(e){if(e.ok&&!e.removed&&e.read===false)unreadBy[e.to]=(unreadBy[e.to]||0)+1;});
function desk(x,y,t,big){var w=big?190:120,h=big?52:40,cur=t.cur,live=t.live;
 var st='empty'; if(live){st=t.occupied_by.some(function(s){return s.live.status==='perm';})?'perm':t.occupied_by.some(function(s){return s.live.status==='busy';})?'busy':'idle';}
 var g='<g class="desk"'+(t.dormant?' opacity="0.5"':'')+'>';
 var tip=t.name+(cur&&cur.s?' S'+cur.s:'')+'\n'+(live?t.occupied_by.map(function(s){return (s.live.status==='busy'?'🟢':s.live.status==='perm'?'🔴許可待ち':'🟡')+' '+(s.live.name||'')+' '+(s.live.model||'')+(s.s?' S'+s.s:'')+(s.live.title?'\n   '+s.live.title:'');}).join('\n'):t.dormant?'停止中（この時間幅にセッションなし）':'空席（稼働セッションなし）')+(t.sessions.length?'\nセッション履歴: '+t.sessions.map(function(s){return s.s?'S'+s.s:'S?';}).join(' '):'');
 g+='<title>'+esc(tip)+'</title>';
 if(live){t.occupied_by.slice(0,big?3:2).forEach(function(s,i){g+='<g class="'+(s.live.status==='busy'?'typing':s.live.status==='perm'?'perm':'')+'">'+sprite(x-w/2+14+i*44,y-62,MODEL_COL(s.live.model),s.live.model,s.live.status,t.name)+'</g>';});}
 else{g+='<rect x="'+(x-14)+'" y="'+(y-30)+'" width="28" height="22" fill="#3a3e58"/><rect x="'+(x-14)+'" y="'+(y-8)+'" width="28" height="6" fill="#2a2d44"/>';}
 g+='<rect x="'+(x-w/2)+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="#8b5a2b"/><rect x="'+(x-w/2)+'" y="'+y+'" width="'+w+'" height="8" fill="#b57a3e"/>';
 var nm=big?2:1;for(var i=0;i<nm;i++){var mx=x-(nm-1)*22+i*44-16;g+='<rect x="'+mx+'" y="'+(y+10)+'" width="32" height="22" fill="#111"/><rect x="'+(mx+2)+'" y="'+(y+12)+'" width="28" height="18" fill="'+SCR[st]+'"/>';if(st==='busy')g+='<rect class="cur" x="'+(mx+6)+'" y="'+(y+22)+'" width="4" height="4" fill="#fff"/>';}
 var label=t.name+(cur&&cur.s?' S'+cur.s:'');
 g+='<rect x="'+(x-w/2)+'" y="'+(y+h+4)+'" width="'+w+'" height="18" fill="#f3efe0"/><text x="'+x+'" y="'+(y+h+17)+'" text-anchor="middle" font-size="'+(label.length>12?10:12)+'" fill="#222">'+esc(label)+'</text>';
 if(t.occupied_by.length>1)g+='<text x="'+(x+w/2-4)+'" y="'+(y+h+17)+'" text-anchor="end" font-size="10" fill="#777">+'+(t.occupied_by.length-1)+'</text>';
 var un=unreadBy[t.name]||0;
 if(un)g+='<rect x="'+(x+w/2-30)+'" y="'+(y-14)+'" width="30" height="14" fill="#f5c542"/><text x="'+(x+w/2-15)+'" y="'+(y-3)+'" text-anchor="middle" font-size="11" fill="#222">📨'+un+'</text>';
 return g+'</g>';}
var pos={}, floors='', walls='', door={}, trackRoom={}, hubVisible=true;
var ROOMS=D.rooms||[];
// ---------- ビル＝テナント（部屋・S22）。?ws=〈部屋名の一部〉が最優先・無ければ localStorage（ヘッダのボタンで保存）・既定=全館 ----------
var wsSel=''; try{wsSel=localStorage.getItem('csv-office-ws')||'';}catch(e){}
// 🧲 ハブ寄せ（S22）: 稼働中の席を島の中でハブに近いマスから詰める。既定=cfg.office.live_near_hub・ヘッダのボタンで切替（localStorage）
var MAGNET=!!D.live_near_hub; try{var mg=localStorage.getItem('csv-office-magnet');if(mg!==null)MAGNET=(mg==='1');}catch(e){}
var TL={}; D.tracks.forEach(function(t){TL[t.name]=t;});
function liveRank(n){var t=TL[n];return (t&&(t.live||(t.occupied_by&&t.occupied_by.length)))?0:1;}
try{var wsQ=new URLSearchParams(location.search).get('ws');if(wsQ!==null)wsSel=wsQ;}catch(e){}
function roomKey(s){return String(s||'').replace(/\s+/g,'').toLowerCase();}
function roomMatch(r,q){return !q||roomKey(r.name).indexOf(roomKey(q))>=0;}
var shown=ROOMS.filter(function(r){return roomMatch(r,wsSel);}); if(ROOMS.length&&!shown.length){wsSel='';shown=ROOMS.slice();}
var hidden=ROOMS.filter(function(r){return shown.indexOf(r)<0;});
function roomTracks(r){var ts=r.main?[HUB]:(r.tracks||[]).slice();if(r.main)(r.islands||[]).forEach(function(i){ts=ts.concat(i.tracks);});return ts;}
ROOMS.forEach(function(r){roomTracks(r).forEach(function(t){trackRoom[t]=r.name;});});
function doorOf(name){var rn=trackRoom[name];return (rn&&door[rn])||null;}
(function(){var el=document.getElementById('wsf');if(!el)return;var h='';
 if(ROOMS.length)[['','🏢 全館']].concat(ROOMS.map(function(r){return [r.name,r.name];})).forEach(function(o){var on=o[0]===''?!wsSel:(shown.length===1&&shown[0].name===o[0]);h+='<button type="button" class="tgl wsb'+(on?' on':'')+'" data-ws="'+esc(o[0])+'">'+esc(o[1])+'</button>';});
 h+='<button type="button" id="magnet" class="tgl wsb'+(MAGNET?' on':'')+'" title="稼働中の席を島の中でハブ側のマスから詰める（クリックで切替）">🧲 ハブ寄せ</button>';
 el.innerHTML=h; el.querySelectorAll('.wsb[data-ws]').forEach(function(b){b.onclick=function(){var v=b.dataset.ws;try{localStorage.setItem('csv-office-ws',v);}catch(e){}var u=new URL(location.href);if(v)u.searchParams.set('ws',v);else u.searchParams.delete('ws');location.href=u.toString();};});
 var mb=document.getElementById('magnet');if(mb)mb.onclick=function(){try{localStorage.setItem('csv-office-magnet',MAGNET?'0':'1');}catch(e){}location.reload();};})();
var byName={}; D.tracks.forEach(function(t){byName[t.name]=t;}); byName[HUB]=D.hub;
function roomCount(r){var ts=roomTracks(r),live=0;ts.forEach(function(n){var t=byName[n];if(t)live+=(t.occupied_by||[]).length;});return ts.length+'席・'+live+'人';}
var PX=166,PY=135,PAD=22,TITLE=26,G=48,M=24,RP=18,RT=36,RG=44;
var ISL_COL=['#f5c542','#39d353','#5ad1e6','#e66fd1','#f97316','#a78bfa','#94a3b8','#22c55e'];
// 島レイアウト: 3×3 のセル。中央＝ハブ。島は config の順に r,l,t,b,tr,tl,br,bl のセルへ（slot 指定があれば優先）。
// 同じセルに複数の島が来たら縦に積む。セルの幅・高さは中の島の最大で決める＝重ならない。原点 (OX,OY) からの相対で組む
function layoutIslands(islands,OX,OY){
 var SLOT={r:[2,1],l:[0,1],t:[1,0],b:[1,2],tr:[2,0],tl:[0,0],br:[2,2],bl:[0,2]}, ORDER=['r','l','t','b','tr','tl','br','bl'];
 var cells={}; for(var cx0=0;cx0<3;cx0++)for(var cy0=0;cy0<3;cy0++)cells[cx0+','+cy0]=[];
 islands.forEach(function(isl,i){var sl=SLOT[isl.slot]||SLOT[ORDER[i%ORDER.length]];var m=isl.tracks.length,cols=Math.min(m,Math.max(Math.ceil(Math.sqrt(m)),Math.ceil(m/2))),rows=Math.ceil(m/cols);
  isl.cols=cols;isl.rows=rows;isl.w=(cols-1)*PX+120+PAD*2+30;isl.h=rows*PY-11+PAD*2+TITLE;cells[sl[0]+','+sl[1]].push(isl);});
 var HUBW=260,HUBH=200, colW=[0,HUBW,0], rowH=[0,HUBH,0];
 Object.keys(cells).forEach(function(k){var c=k.split(',').map(Number),L=cells[k];if(!L.length)return;var w=Math.max.apply(null,L.map(function(x){return x.w;})),h=L.reduce(function(a,x){return a+x.h;},0)+G*(L.length-1);
  colW[c[0]]=Math.max(colW[c[0]],w);rowH[c[1]]=Math.max(rowH[c[1]],h);});
 var colX=[OX,OX+colW[0]+(colW[0]?G:0)], rowY=[OY,OY+rowH[0]+(rowH[0]?G:0)]; colX[2]=colX[1]+colW[1]+G; rowY[2]=rowY[1]+rowH[1]+G;
 var hx=colX[1]+colW[1]/2, hy=rowY[1]+rowH[1]/2+30;  // ハブの中心（🧲 の距離の基準）
 Object.keys(cells).forEach(function(k){var c=k.split(',').map(Number),L=cells[k];if(!L.length)return;var tot=L.reduce(function(a,x){return a+x.h;},0)+G*(L.length-1);
  var y=c[1]===0?rowY[0]+rowH[0]-tot:c[1]===2?rowY[2]:rowY[1]+(rowH[1]-tot)/2;
  L.forEach(function(isl){var x=c[0]===0?colX[0]+colW[0]-isl.w:c[0]===2?colX[2]:colX[1]+(colW[1]-isl.w)/2;var col=ISL_COL[islands.indexOf(isl)%ISL_COL.length];
   floors+='<rect x="'+x+'" y="'+y+'" width="'+isl.w+'" height="'+isl.h+'" rx="12" fill="'+col+'" fill-opacity="0.10" stroke="'+col+'" stroke-opacity="0.45" stroke-width="2"/><text x="'+(x+12)+'" y="'+(y+18)+'" font-size="13" font-weight="600" fill="'+col+'">'+esc(isl.name)+'</text><text x="'+(x+isl.w-10)+'" y="'+(y+18)+'" text-anchor="end" font-size="11" fill="'+col+'" fill-opacity="0.8">'+isl.tracks.length+'席</text>';
   var cellsOf=[];for(var k=0;k<isl.tracks.length;k++){var cc=k%isl.cols,rr=Math.floor(k/isl.cols);cellsOf.push([Math.round(x+PAD+60+cc*PX),Math.round(y+PAD+TITLE+62+rr*PY)]);}
   var names=isl.tracks.slice();if(MAGNET){cellsOf.sort(function(a,b){return Math.hypot(a[0]-hx,a[1]-hy)-Math.hypot(b[0]-hx,b[1]-hy);});names.sort(function(a,b){return liveRank(a)-liveRank(b);});}
   names.forEach(function(name,k){pos[name]=cellsOf[k];});
   y+=isl.h+G;});});
 return {w:(colW[2]?colX[2]+colW[2]:colX[1]+colW[1])-OX, h:(rowH[2]?rowY[2]+rowH[2]:rowY[1]+rowH[1])-OY, cx:colX[1]+colW[1]/2, cy:rowY[1]+rowH[1]/2+30};}
// 本業以外の部屋: 島なし・席を格子に並べるだけ
function layoutGrid(tracks,OX,OY){var m=tracks.length;if(!m)return {w:300,h:110,empty:true};var cols=Math.min(m,Math.max(2,Math.ceil(Math.sqrt(m)))),rows=Math.ceil(m/cols);
 tracks.forEach(function(name,k){var cc=k%cols,rr=Math.floor(k/cols);pos[name]=[Math.round(OX+PAD+60+cc*PX),Math.round(OY+PAD+62+rr*PY)];});
 return {w:(cols-1)*PX+120+PAD*2+30,h:rows*PY-11+PAD*2};}
// 部屋＝壁（太枠）＋名札（部屋名・席数・在席）。本業の部屋は島レイアウト・他は格子
function placeRoom(r,X,Y){var inner;
 if(r.main){inner=layoutIslands(r.islands||[],X+RP,Y+RT);CX=inner.cx;CY=inner.cy;hubVisible=true;}
 else inner=layoutGrid(r.tracks||[],X+RP,Y+RT);
 var w=Math.max(inner.w+RP*2,r.name.length*16+140), h=inner.h+RT+RP;
 walls+='<rect x="'+X+'" y="'+Y+'" width="'+w+'" height="'+h+'" rx="16" fill="#000" fill-opacity="0.16" stroke="#cbd5e1" stroke-opacity="0.5" stroke-width="4"/><text x="'+(X+16)+'" y="'+(Y+25)+'" font-size="16" font-weight="700" fill="#e2e8f0">'+esc(r.name)+'</text><text x="'+(X+w-14)+'" y="'+(Y+25)+'" text-anchor="end" font-size="12" fill="#cbd5e1" fill-opacity="0.85">'+esc(roomCount(r))+'</text>';
 if(inner.empty)walls+='<text x="'+(X+w/2)+'" y="'+(Y+RT+62)+'" text-anchor="middle" font-size="13" fill="#94a3b8">空室（この時間幅にセッションなし）</text>';
 return {w:w,h:h};}
if(ROOMS.length){hubVisible=false;
 // 1つ目の部屋を左に・残りは右に縦積み（本業が横長なので）。絞り込み中は shown だけ
 var r0=placeRoom(shown[0],M,M), X2=M+r0.w+RG, ys=M, sideW=0;
 shown.slice(1).forEach(function(r){var s=placeRoom(r,X2,ys);sideW=Math.max(sideW,s.w);ys+=s.h+RG;});
 W=(sideW?X2+sideW:M+r0.w)+M; H=Math.max(M+r0.h,ys-RG)+M;
 // 絞り込みで隠した部屋＝右端のドア。そこ宛／そこ発の矢印は壁の外へ出る線になる
 if(hidden.length){var DX=W;W+=260;H=Math.max(H,M+40+hidden.length*110);
  hidden.forEach(function(r,k){var dp=[DX+230,M+80+k*110];door[r.name]=dp;
   walls+='<g><title>'+esc(r.name+'（絞り込みで非表示・ボタンで表示）')+'</title><rect x="'+(dp[0]-14)+'" y="'+(dp[1]-32)+'" width="28" height="64" rx="4" fill="#8b5a2b" stroke="#cbd5e1" stroke-opacity=".5" stroke-width="2"/><circle cx="'+(dp[0]+6)+'" cy="'+dp[1]+'" r="3" fill="#f5c542"/><text x="'+(dp[0]-22)+'" y="'+(dp[1]+4)+'" text-anchor="end" font-size="11" fill="#cbd5e1">🚪 '+esc(r.name)+'</text></g>';});}
}else if(D.islands&&D.islands.length){var r1=layoutIslands(D.islands,M,M);CX=r1.cx;CY=r1.cy;W=r1.w+M*2;H=r1.h+M*2;
}else{
 D.tracks.forEach(function(t,i){var a=-Math.PI/2+i*2*Math.PI/Math.max(n,1);pos[t.name]=[Math.round(CX+RX*Math.cos(a)),Math.round(CY+RY*Math.sin(a))];});
}
if(hubVisible)pos[HUB]=[CX,CY];
var svg='<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet"><defs><pattern id="tile" width="40" height="40" patternUnits="userSpaceOnUse"><rect width="40" height="40" fill="#2a2d3f"/><rect width="20" height="20" fill="#2f3348"/><rect x="20" y="20" width="20" height="20" fill="#2f3348"/></pattern><marker id="ah" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker></defs><rect width="'+W+'" height="'+H+'" fill="url(#tile)"/>'+walls+floors;
var ts0=D.edges.length?Date.parse(D.edges[0].ts):0, ts1=D.edges.length?Date.parse(D.edges[D.edges.length-1].ts):1;
D.edges.forEach(function(e,i){var A=pos[e.frm]||doorOf(e.frm),B=pos[e.to]||doorOf(e.to);if(!A||!B||(!pos[e.frm]&&!pos[e.to]))return;var x1=A[0],y1=A[1]+20,x2=B[0],y2=B[1]+20;
 var mx=(x1+x2)/2,my=(y1+y2)/2,dx=x2-x1,dy=y2-y1,L=Math.hypot(dx,dy)||1,ox=-dy/L*28,oy=dx/L*28;
 var col=e.to===HUB?'var(--gold)':e.frm===HUB?'var(--cyan)':'var(--mag)'; if(!e.ok)col='var(--red)';
 var rec=(Date.parse(e.ts)-ts0)/((ts1-ts0)||1), op=(0.25+0.75*rec).toFixed(2);
 svg+='<path id="p'+i+'" class="edge'+(e.ok?'':' ng')+(e.removed?' rm':'')+'" data-i="'+i+'" d="M'+x1+','+y1+' Q'+(mx+ox)+','+(my+oy)+' '+x2+','+y2+'" stroke="'+col+'" opacity="'+op+'" marker-end="url(#ah)"><title>'+esc(e.t+' '+e.frm+' → '+e.to+'\n'+e.summary+(e.preview&&e.preview!==e.summary?'\n'+e.preview:''))+'</title></path>';
 svg+='<g id="env'+i+'" opacity="0"><rect x="-8" y="-6" width="16" height="12" fill="#fff" stroke="#333"/><polyline points="-8,-6 0,2 8,-6" fill="none" stroke="#333"/><animateMotion id="am'+i+'" dur="0.9s" begin="indefinite" fill="remove"><mpath href="#p'+i+'"/></animateMotion></g>';});
D.tracks.forEach(function(t){var p=pos[t.name];if(p)svg+=desk(p[0],p[1],t,false);});
if(hubVisible)svg+=desk(CX,CY,D.hub,true);
// 人間＝最後に話しかけた席の横に1人（席には座らない・歩き回る）。前回位置を覚えておき、席が変われば歩いて移動する。
var HS=["....HHHH....","...HHHHHH...","...HSSSSH...","...SSSSSS...","...SESSES...","...SSSSSS...","....SSSS....","..GGGGGGGG..",".GGGGGGGGGG.",".G.GGGGGG.G.",".S.GGGGGG.S.","...GGGGGG...","...KKKKKK...","...KK..KK...","...KK..KK...","...BB..BB..."];
var HP={H:'#111',S:'#f2c9a0',E:'#222',G:'#2e9e6b',K:'#444',B:'#222'};
function hsprite(x,y){if(SP.human)return img(SP.human,x,y,null);var s='',sc=3;HS.forEach(function(row,r){row.split('').forEach(function(c,i){if(c==='.')return;s+='<rect x="'+(x+i*sc)+'" y="'+(y+r*sc)+'" width="'+sc+'" height="'+sc+'" fill="'+HP[c]+'"/>';});});return s;}
function hpos(track){var p=pos[track];if(p){var w=track===HUB?190:120;return [p[0]+w/2+6,p[1]-50];}var d=doorOf(track);return d?[d[0]-190,d[1]-50]:null;}  // 隠した部屋ならドアの前
if(D.human&&hpos(D.human.track)){var hp=hpos(D.human.track),prev=null;try{prev=JSON.parse(localStorage.getItem('csv-human-at')||'null');}catch(e){}
 var from=(prev&&prev.track!==D.human.track)?hpos(prev.track):null;
 var mins=Math.max(0,Math.round((Date.now()-D.human.ts*1000)/60000)), ago=mins<1?'いま':mins<60?mins+'分前':Math.floor(mins/60)+'時間'+(mins%60)+'分前';
 var tip=D.human.name+'：最後に話しかけた席＝'+D.human.track+(D.human.s?' S'+D.human.s:'')+'（'+D.human.t+'・'+ago+'）\n「'+D.human.text+'」';
 var dp=pos[D.human.track],dw=D.human.track===HUB?190:120,dh=D.human.track===HUB?52:40;
 if(dp)svg+='<rect x="'+(dp[0]-dw/2-6)+'" y="'+(dp[1]-70)+'" width="'+(dw+12)+'" height="'+(dh+98)+'" rx="8" fill="#2e9e6b" fill-opacity="0.10" stroke="#2e9e6b" stroke-dasharray="6 4" stroke-width="2"/>';
 var hname=D.human.name+(dp?'':'→'+(trackRoom[D.human.track]||'別の部屋'));  // 隠した部屋にいる時は部屋名を添える
 svg+='<g class="human"><title>'+esc(tip)+'</title>'+hsprite(hp[0],hp[1])+
  '<rect x="'+(hp[0]-6)+'" y="'+(hp[1]-22)+'" width="'+(hname.length*11+ago.length*7+30)+'" height="17" rx="3" fill="#fff" stroke="#333"/><text x="'+(hp[0]-2)+'" y="'+(hp[1]-9)+'" font-size="11" fill="#222">👤 '+esc(hname)+' '+esc(ago)+'</text>'+
  (from?'<animateTransform attributeName="transform" type="translate" from="'+(from[0]-hp[0])+' '+(from[1]-hp[1])+'" to="0 0" dur="1.4s" fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1"/>':'')+'</g>';
 try{localStorage.setItem('csv-human-at',JSON.stringify({track:D.human.track}));}catch(e){}}
Object.keys(tints).forEach(function(id){svg+='<filter id="'+id+'" x="0" y="0" width="1" height="1"><feFlood flood-color="'+tints[id]+'" result="c"/><feComposite in="c" in2="SourceAlpha" operator="in" result="m"/><feBlend in="m" in2="SourceGraphic" mode="multiply"/></filter>';});
svg+='</svg>';
document.getElementById('scene').innerHTML=svg;
var log=document.getElementById('log');
// 並び順: 古い→新しい（最新が下・チャット式）／新しい→古い（最新が上）。localStorage で記憶。日付見出しは順に合わせて組み直す
var ord=document.getElementById('ord'), desc=false; try{desc=localStorage.getItem('csv-log-desc')==='1';}catch(e){}
function renderLog(){log.innerHTML='';var lastDay='';
 if(!D.edges.length){log.innerHTML='<div class="empty">直近 '+D.window_hours+'h にセッション間メッセージはない</div>';return;}
 var idx=D.edges.map(function(_,i){return i;}); if(desc)idx.reverse();
 idx.forEach(function(i){var e=D.edges[i];if(e.day!==lastDay){lastDay=e.day;var d=document.createElement('div');d.className='day';d.textContent=e.day;log.appendChild(d);}
  var r=document.createElement('div');r.className='row';r.dataset.i=i;
  var st=!e.ok?' <span class="ng">❌不達</span>':e.removed?' <span class="rm">🗑破棄</span>':e.read===false?' <span class="un">📨未読</span>':'';
  r.innerHTML='<span class="t">'+e.t.slice(6)+'</span><span class="who">'+esc(e.frm+(e.frm_s?' S'+e.frm_s:''))+'</span> → <span class="who">'+esc(e.to+(e.to_s?' S'+e.to_s:''))+'</span>'+st+'<br><span class="sm">'+esc(e.summary)+'</span>';
  r.title=e.preview; r.onclick=function(){hl(i);}; log.appendChild(r);});
 ord.textContent=desc?'⇅ 最新が上':'⇅ 最新が下'; log.scrollTop=desc?0:log.scrollHeight;}
function hl(i){document.querySelectorAll('.hl').forEach(function(x){x.classList.remove('hl');});var p=document.getElementById('p'+i),r=document.querySelector('.row[data-i="'+i+'"]');if(p){p.classList.add('hl');p.parentNode.appendChild(p);}if(r){r.classList.add('hl');r.scrollIntoView({block:'nearest'});}}
document.querySelectorAll('.edge').forEach(function(p){p.onclick=function(){hl(+p.dataset.i);};});
document.getElementById('play').onclick=function(){D.edges.forEach(function(e,i){setTimeout(function(){hl(i);var g=document.getElementById('env'+i),a=document.getElementById('am'+i);if(!g)return;g.setAttribute('opacity','1');a.beginElement();setTimeout(function(){g.setAttribute('opacity','0');},900);},i*950);});};
ord.onclick=function(){desc=!desc;try{localStorage.setItem('csv-log-desc',desc?'1':'0');}catch(e){}renderLog();};
renderLog();
})();
"""

_CODE_VER = hashlib.sha1((CSS + PAGE_JS).encode()).hexdigest()[:8]


def render_office(D: dict, cfg: dict, stamp: str, poll_ms: int) -> str:
    hub = (cfg.get("office") or {}).get("hub", "業務ハブ")
    c = D["counts"]
    now = datetime.now().strftime("%m/%d %H:%M:%S")
    tiles = (f'<div class="tiles"><span class="tile"><b>{c["live"]}</b>人 在席</span>'
             f'<span class="tile"><b>{c["edges"]}</b>通 /{D["window_hours"]}h</span>'
             f'<span class="tile{" warn" if c["unread"] else ""}"><b>{c["unread"]}</b>未読</span>'
             f'<span class="tile{" bad" if c["failed"] else ""}"><b>{c["failed"]}</b>不達</span></div>')
    legend = ('<span class="legend"><span><i class="sw" style="background:var(--gold)"></i>→ハブ</span>'
              '<span><i class="sw" style="background:var(--cyan)"></i>ハブ→</span>'
              '<span><i class="sw" style="background:var(--mag)"></i>トラック間</span>'
              '<span><i class="sw" style="background:var(--red)"></i>不達（破線）</span>'
              '<span>｜ 画面 <i class="sw" style="background:#39d353"></i>作業中 '
              '<i class="sw" style="background:#4aa3ff"></i>待機 '
              '<i class="sw" style="background:#e5533b"></i>許可待ち '
              '<i class="sw" style="background:#555"></i>空席</span>'
              '<span>｜ シャツ <i class="sw" style="background:#8a5cf6"></i>Opus '
              '<i class="sw" style="background:#3b82f6"></i>Sonnet '
              '<i class="sw" style="background:#d4a017"></i>Haiku '
              '<i class="sw" style="background:#f472b6"></i>Fable</span>'
              '<span>｜ <i class="sw" style="background:#2e9e6b"></i>👤 人間＝最後に話しかけた席の横（吹き出し=経過）</span>'
              '<span>｜ 🎨 <select id="spr" title="絵のテーマ（assets/sprites/<テーマ>/）"></select></span>'
              '<span id="wsf" title="部屋（ワークスペース）で絞る・?ws=〈部屋名の一部〉でも指定可"></span></span>')
    js = (f"var D={json.dumps(D, ensure_ascii=False)},STAMP={json.dumps(stamp)},"
          f"POLL_MS={poll_ms},HUB={json.dumps(hub)};" + PAGE_JS)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🏢 バーチャルオフィス</title>
<script>{HEAD_JS}</script>
<style>{CSS}</style></head>
<body>
<header><div><h1>🏢 バーチャルオフィス</h1>
<div class="sub">いま <b id="clock"></b> ／ 生成 {now} ／ 席=トラック・人=稼働セッション・矢印=SendMessage（新しいほど濃い）・席にマウスで詳細</div></div>
{tiles}
<button class="tgl" id="play" type="button">▶ 流れを再生</button>
<div class="navs">{nav_html("office")}<button class="tgl" id="tgl" type="button"></button></div>
<div style="flex-basis:100%">{legend}</div>
</header>
<main><div id="scene"></div><aside><h2>📨 やり取り（クリックで矢印を強調）<button class="tgl" id="ord" type="button"></button></h2><div id="log"></div></aside></main>
<footer>live_board.py --html --watch が同じループで書き換え中（変化があった時だけ・数秒〜10秒で反映）。ローカル閲覧専用＝案件名が入るので公開しない。</footer>
<script>{js}</script>
</body></html>"""


def write_office(cfg: dict, out_dir: Path, res: dict, labels: dict, rows,
                 poll_ms: int = 5000) -> bool:
    """office.html を out_dir に書く。中身（D）が変わった時だけ書いて True。"""
    D = build_office_data(res, dict(labels), rows, cfg)
    payload = json.dumps(D, ensure_ascii=False, sort_keys=True)
    stamp = hashlib.sha1((payload + _CODE_VER).encode()).hexdigest()[:16]
    stamp_path = out_dir / "office_stamp.js"
    stamp_js = f"window.__officeStamp&&window.__officeStamp({json.dumps(stamp)});"
    out_path = out_dir / "office.html"
    try:
        if out_path.exists() and stamp_path.read_text(encoding="utf-8") == stamp_js:
            return False
    except OSError:
        pass
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_office(D, cfg, stamp, poll_ms), encoding="utf-8")
    stamp_path.write_text(stamp_js, encoding="utf-8")
    return True
