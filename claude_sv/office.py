"""バーチャルオフィス配置図（reports/office.html・S21 段階②）。

席＝トラック（ProjectMapper の案件名）・人＝稼働中セッション（シャツ色＝モデル）・矢印＝SendMessage。
中央の大きい席が業務ハブ。データは messages.collect（送受信の突合）と live_board の rows（稼働状態）だけ
＝新しい記録場所は作らない。ローカル閲覧専用（案件名・要約が入るので公開しない）。

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


def build_office_data(res: dict, labels: dict, rows, cfg: dict) -> dict:
    """messages.collect の結果＋ラベル＋稼働 rows から、描画用の D を組む（JS 側は配置と描画だけ）。"""
    hub_disp = (cfg.get("office") or {}).get("hub", "業務ハブ")
    hubs = hub_names(cfg)
    registry = res.get("registry") or {}

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
                     "model": r.get("model") or "", "title": (r.get("title") or "")[:60],
                     "last_human": r.get("last_human")}
        if sid not in labels and r.get("project") and r["project"] != "—":
            labels[sid] = {"project": r["project"], "s": r.get("session_no")}

    tracks: dict = {}
    for sid in set(labels) | set(live):
        p = proj(sid) or "（未分類）"
        t = tracks.setdefault(p, {"name": p, "sessions": []})
        t["sessions"].append({"sid": sid, "s": (labels.get(sid) or {}).get("s"),
                              "live": live.get(sid)})
    for t in tracks.values():
        t["sessions"].sort(key=lambda x: (x["live"] is None, -(x["s"] or 0)))
        t["occupied_by"] = [x for x in t["sessions"] if x["live"]]
        t["live"] = bool(t["occupied_by"])
        t["cur"] = t["sessions"][0] if t["sessions"] else None
    hub = tracks.pop(hub_disp, {"name": hub_disp, "sessions": [], "occupied_by": [],
                                "live": False, "cur": None})
    order = sorted(tracks.values(), key=lambda t: (not t["live"], t["name"]))

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
        fp, tp = proj(e["frm"]), proj(e["to"])
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
    return {"hub": hub, "tracks": order, "edges": edges, "human": human, "sprites": load_sprites(),
            "counts": {"edges": len(edges), "unread": unread, "failed": failed,
                       "live": sum(len(t["occupied_by"]) for t in order) + len(hub["occupied_by"])},
            "window_hours": (cfg.get("office") or {}).get("window_hours", 48)}


CSS = """
*{box-sizing:border-box}
:root{color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
  --border:rgba(255,255,255,.10);--wash:rgba(255,255,255,.055);--gold:#f5c542;--cyan:#5ad1e6;--mag:#e66fd1;--red:#e5533b}
:root[data-theme="light"]{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --border:rgba(11,11,11,.10);--wash:rgba(11,11,11,.045);--gold:#b8860b;--cyan:#1d8fa6;--mag:#b3399e;--red:#c2341f}
body{margin:0;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;font-size:13px;line-height:1.5}
header{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:600} .sub{color:var(--muted);font-size:11.5px}
.navs{margin-left:auto;display:flex;gap:6px;align-items:center}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;padding:3px 11px;font-size:12px;cursor:pointer;text-decoration:none;font-family:inherit}
.tgl:hover{background:var(--wash);color:var(--ink)} .tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
main{display:grid;grid-template-columns:1fr 380px;height:calc(100vh - 50px)}
#scene{overflow:auto;background:#1b1d2a} svg{display:block;shape-rendering:crispEdges}
aside{background:var(--surface);border-left:1px solid var(--border);overflow:auto;padding:10px 12px}
aside h2{font-size:12.5px;margin:4px 0 8px;color:var(--muted);font-weight:600}
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
footer{padding:6px 16px;color:var(--muted);font-size:11px;border-top:1px solid var(--border)}
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
var SP=D.sprites||{}, tints={};
function img(href,x,y,filt){return '<image href="'+href+'" x="'+x+'" y="'+y+'" width="36" height="48" preserveAspectRatio="xMidYMax meet" style="image-rendering:pixelated"'+(filt?' filter="url(#'+filt+')"':'')+'/>';}
function sprite(x,y,shirt,model){var key=model&&SP['agent-'+(/opus/.test(model)?'opus':/sonnet/.test(model)?'sonnet':/haiku/.test(model)?'haiku':/fable/.test(model)?'fable':'')];
 if(key)return img(key,x,y,null);
 if(SP.agent){var id='tint'+shirt.replace('#','');tints[id]=shirt;return img(SP.agent,x,y,id);}
 var s='',sc=3;SPR.forEach(function(row,r){row.split('').forEach(function(c,i){if(c==='.')return;var col=(c==='T'||c==='A')?shirt:PAL[c];s+='<rect x="'+(x+i*sc)+'" y="'+(y+r*sc)+'" width="'+sc+'" height="'+sc+'" fill="'+col+'"/>';});});return s;}
var SCR={busy:'#39d353',idle:'#4aa3ff',perm:'#e5533b',empty:'#555'};
var unreadBy={}; D.edges.forEach(function(e){if(e.ok&&!e.removed&&e.read===false)unreadBy[e.to]=(unreadBy[e.to]||0)+1;});
function desk(x,y,t,big){var w=big?190:120,h=big?52:40,cur=t.cur,live=t.live;
 var st='empty'; if(live){st=t.occupied_by.some(function(s){return s.live.status==='perm';})?'perm':t.occupied_by.some(function(s){return s.live.status==='busy';})?'busy':'idle';}
 var g='<g class="desk">';
 var tip=t.name+(cur&&cur.s?' S'+cur.s:'')+'\n'+(live?t.occupied_by.map(function(s){return (s.live.status==='busy'?'🟢':s.live.status==='perm'?'🔴許可待ち':'🟡')+' '+(s.live.name||'')+' '+(s.live.model||'')+(s.s?' S'+s.s:'')+(s.live.title?'\n   '+s.live.title:'');}).join('\n'):'空席（稼働セッションなし）')+'\nセッション履歴: '+t.sessions.map(function(s){return s.s?'S'+s.s:'S?';}).join(' ');
 g+='<title>'+esc(tip)+'</title>';
 if(live){t.occupied_by.slice(0,big?3:2).forEach(function(s,i){g+='<g class="'+(s.live.status==='busy'?'typing':s.live.status==='perm'?'perm':'')+'">'+sprite(x-w/2+14+i*44,y-62,MODEL_COL(s.live.model),s.live.model)+'</g>';});}
 else{g+='<rect x="'+(x-14)+'" y="'+(y-30)+'" width="28" height="22" fill="#3a3e58"/><rect x="'+(x-14)+'" y="'+(y-8)+'" width="28" height="6" fill="#2a2d44"/>';}
 g+='<rect x="'+(x-w/2)+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="#8b5a2b"/><rect x="'+(x-w/2)+'" y="'+y+'" width="'+w+'" height="8" fill="#b57a3e"/>';
 var nm=big?2:1;for(var i=0;i<nm;i++){var mx=x-(nm-1)*22+i*44-16;g+='<rect x="'+mx+'" y="'+(y+10)+'" width="32" height="22" fill="#111"/><rect x="'+(mx+2)+'" y="'+(y+12)+'" width="28" height="18" fill="'+SCR[st]+'"/>';if(st==='busy')g+='<rect class="cur" x="'+(mx+6)+'" y="'+(y+22)+'" width="4" height="4" fill="#fff"/>';}
 var label=t.name+(cur&&cur.s?' S'+cur.s:'');
 g+='<rect x="'+(x-w/2)+'" y="'+(y+h+4)+'" width="'+w+'" height="18" fill="#f3efe0"/><text x="'+x+'" y="'+(y+h+17)+'" text-anchor="middle" font-size="'+(label.length>12?10:12)+'" fill="#222">'+esc(label)+'</text>';
 if(t.occupied_by.length>1)g+='<text x="'+(x+w/2-4)+'" y="'+(y+h+17)+'" text-anchor="end" font-size="10" fill="#777">+'+(t.occupied_by.length-1)+'</text>';
 var un=unreadBy[t.name]||0;
 if(un)g+='<rect x="'+(x+w/2-30)+'" y="'+(y-14)+'" width="30" height="14" fill="#f5c542"/><text x="'+(x+w/2-15)+'" y="'+(y-3)+'" text-anchor="middle" font-size="11" fill="#222">📨'+un+'</text>';
 return g+'</g>';}
var pos={}; pos[HUB]=[CX,CY];
D.tracks.forEach(function(t,i){var a=-Math.PI/2+i*2*Math.PI/Math.max(n,1);pos[t.name]=[Math.round(CX+RX*Math.cos(a)),Math.round(CY+RY*Math.sin(a))];});
var svg='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'"><defs><pattern id="tile" width="40" height="40" patternUnits="userSpaceOnUse"><rect width="40" height="40" fill="#2a2d3f"/><rect width="20" height="20" fill="#2f3348"/><rect x="20" y="20" width="20" height="20" fill="#2f3348"/></pattern><marker id="ah" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker></defs><rect width="'+W+'" height="'+H+'" fill="url(#tile)"/>';
var ts0=D.edges.length?Date.parse(D.edges[0].ts):0, ts1=D.edges.length?Date.parse(D.edges[D.edges.length-1].ts):1;
D.edges.forEach(function(e,i){var A=pos[e.frm],B=pos[e.to];if(!A||!B)return;var x1=A[0],y1=A[1]+20,x2=B[0],y2=B[1]+20;
 var mx=(x1+x2)/2,my=(y1+y2)/2,dx=x2-x1,dy=y2-y1,L=Math.hypot(dx,dy)||1,ox=-dy/L*28,oy=dx/L*28;
 var col=e.to===HUB?'var(--gold)':e.frm===HUB?'var(--cyan)':'var(--mag)'; if(!e.ok)col='var(--red)';
 var rec=(Date.parse(e.ts)-ts0)/((ts1-ts0)||1), op=(0.25+0.75*rec).toFixed(2);
 svg+='<path id="p'+i+'" class="edge'+(e.ok?'':' ng')+(e.removed?' rm':'')+'" data-i="'+i+'" d="M'+x1+','+y1+' Q'+(mx+ox)+','+(my+oy)+' '+x2+','+y2+'" stroke="'+col+'" opacity="'+op+'" marker-end="url(#ah)"><title>'+esc(e.t+' '+e.frm+' → '+e.to+'\n'+e.summary+(e.preview&&e.preview!==e.summary?'\n'+e.preview:''))+'</title></path>';
 svg+='<g id="env'+i+'" opacity="0"><rect x="-8" y="-6" width="16" height="12" fill="#fff" stroke="#333"/><polyline points="-8,-6 0,2 8,-6" fill="none" stroke="#333"/><animateMotion id="am'+i+'" dur="0.9s" begin="indefinite" fill="remove"><mpath href="#p'+i+'"/></animateMotion></g>';});
D.tracks.forEach(function(t){var p=pos[t.name];svg+=desk(p[0],p[1],t,false);});
svg+=desk(CX,CY,D.hub,true);
// 人間＝最後に話しかけた席の横に1人（席には座らない・歩き回る）。前回位置を覚えておき、席が変われば歩いて移動する。
var HS=["....HHHH....","...HHHHHH...","...HSSSSH...","...SSSSSS...","...SESSES...","...SSSSSS...","....SSSS....","..GGGGGGGG..",".GGGGGGGGGG.",".G.GGGGGG.G.",".S.GGGGGG.S.","...GGGGGG...","...KKKKKK...","...KK..KK...","...KK..KK...","...BB..BB..."];
var HP={H:'#111',S:'#f2c9a0',E:'#222',G:'#2e9e6b',K:'#444',B:'#222'};
function hsprite(x,y){if(SP.human)return img(SP.human,x,y,null);var s='',sc=3;HS.forEach(function(row,r){row.split('').forEach(function(c,i){if(c==='.')return;s+='<rect x="'+(x+i*sc)+'" y="'+(y+r*sc)+'" width="'+sc+'" height="'+sc+'" fill="'+HP[c]+'"/>';});});return s;}
function hpos(track){var p=pos[track];if(!p)return null;var w=track===HUB?190:120;return [p[0]+w/2+10,p[1]-50];}
if(D.human&&pos[D.human.track]){var hp=hpos(D.human.track),prev=null;try{prev=JSON.parse(localStorage.getItem('csv-human-at')||'null');}catch(e){}
 var from=(prev&&prev.track!==D.human.track)?hpos(prev.track):null;
 var mins=Math.max(0,Math.round((Date.now()-D.human.ts*1000)/60000)), ago=mins<1?'いま':mins<60?mins+'分前':Math.floor(mins/60)+'時間'+(mins%60)+'分前';
 var tip=D.human.name+'：最後に話しかけた席＝'+D.human.track+(D.human.s?' S'+D.human.s:'')+'（'+D.human.t+'・'+ago+'）\n「'+D.human.text+'」';
 svg+='<g class="human"><title>'+esc(tip)+'</title>'+hsprite(hp[0],hp[1])+
  '<rect x="'+(hp[0]-6)+'" y="'+(hp[1]-22)+'" width="'+(D.human.name.length*11+ago.length*7+30)+'" height="17" rx="3" fill="#fff" stroke="#333"/><text x="'+(hp[0]-2)+'" y="'+(hp[1]-9)+'" font-size="11" fill="#222">👤 '+esc(D.human.name)+' '+esc(ago)+'</text>'+
  (from?'<animateTransform attributeName="transform" type="translate" from="'+(from[0]-hp[0])+' '+(from[1]-hp[1])+'" to="0 0" dur="1.4s" fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1"/>':'')+'</g>';
 try{localStorage.setItem('csv-human-at',JSON.stringify({track:D.human.track}));}catch(e){}}
Object.keys(tints).forEach(function(id){svg+='<filter id="'+id+'" x="0" y="0" width="1" height="1"><feFlood flood-color="'+tints[id]+'" result="c"/><feComposite in="c" in2="SourceAlpha" operator="in" result="m"/><feBlend in="m" in2="SourceGraphic" mode="multiply"/></filter>';});
svg+='</svg>';
document.getElementById('scene').innerHTML=svg;
var log=document.getElementById('log'), lastDay='';
if(!D.edges.length)log.innerHTML='<div class="empty">直近 '+D.window_hours+'h にセッション間メッセージはない</div>';
D.edges.forEach(function(e,i){if(e.day!==lastDay){lastDay=e.day;var d=document.createElement('div');d.className='day';d.textContent=e.day;log.appendChild(d);}
 var r=document.createElement('div');r.className='row';r.dataset.i=i;
 var st=!e.ok?' <span class="ng">❌不達</span>':e.removed?' <span class="rm">🗑破棄</span>':e.read===false?' <span class="un">📨未読</span>':'';
 r.innerHTML='<span class="t">'+e.t.slice(6)+'</span><span class="who">'+esc(e.frm+(e.frm_s?' S'+e.frm_s:''))+'</span> → <span class="who">'+esc(e.to+(e.to_s?' S'+e.to_s:''))+'</span>'+st+'<br><span class="sm">'+esc(e.summary)+'</span>';
 r.title=e.preview; r.onclick=function(){hl(i);}; log.appendChild(r);});
function hl(i){document.querySelectorAll('.hl').forEach(function(x){x.classList.remove('hl');});var p=document.getElementById('p'+i),r=document.querySelector('.row[data-i="'+i+'"]');if(p){p.classList.add('hl');p.parentNode.appendChild(p);}if(r){r.classList.add('hl');r.scrollIntoView({block:'nearest'});}}
document.querySelectorAll('.edge').forEach(function(p){p.onclick=function(){hl(+p.dataset.i);};});
document.getElementById('play').onclick=function(){D.edges.forEach(function(e,i){setTimeout(function(){hl(i);var g=document.getElementById('env'+i),a=document.getElementById('am'+i);if(!g)return;g.setAttribute('opacity','1');a.beginElement();setTimeout(function(){g.setAttribute('opacity','0');},900);},i*950);});};
log.scrollTop=log.scrollHeight;
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
              '<span>｜ <i class="sw" style="background:#2e9e6b"></i>👤 人間＝最後に話しかけた席の横（吹き出し=経過）</span></span>')
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
<main><div id="scene"></div><aside><h2>📨 やり取り（時刻順・クリックで矢印を強調）</h2><div id="log"></div></aside></main>
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
