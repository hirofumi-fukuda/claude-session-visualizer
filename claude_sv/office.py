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
☕ 休憩室（S22）: cfg.office.lounge {name, slot（既定 tr）, break_after_min（既定 20）} で島の空きセルに休憩室（ソファ・コーヒー・
自販機・観葉植物・窓＝時刻で昼夕夜・ホワイトボード＝在席/未読・印刷機コーナー＝作業中の席があれば用紙が出る）。最後の発話から
break_after_min 分で人間のスプライトが休憩室へ歩く（話しかけると席へ戻る・戻る席の点線枠は残す）。各島の角に観葉植物（飾り）。
小物の絵は assets/props/<名前>.png があればそれ・無ければコード内の矩形描画（受け口が先・絵は後から差し替え）。
🪑 hub_side（S22）: cfg.office.hub_side＝トラック名リスト。島に入れずハブの右隣に小机で並べる（同じ部屋・採番なしの運用型＝会議サポート等）。
📱 Remote Control（S22）: 稼働台帳 sessions/<pid>.json に bridgeSessionId があれば RC オン＝席の右上に 📱（外出先から指示できる席・ハブ 10/15 発見）。
🎨 配色（S22）: 図もページのテーマ（☀/☾・localStorage csv-theme＝他ページと共通）に追従。light＝明るいオフィス（白い壁・淡いタイル床・ベージュのカーペット）。オフィスは保存値が無ければ light・?theme=light|dark で一時上書き（保存しない・スクショ用）。
🧾 ホワイトボード（S23・ハブ提案 10/15）: 休憩室の壁いっぱいに今日の daily の要点＝🔴 TOP 見出し3行・⏰ 次の時刻固定イベント・⏸ 返信待ち（件数と相手）・
端に在席/未読/不達。中身は daily_view.load_board（機械的に取れる分だけ・新しい記録場所は作らない）。クリックで 📓 日誌。幅か高さが足りない時は S22 の小さい板。
🔔 新着の矢印（S23）: 直近 15 分の矢印は流れる破線で動く（同じ席ペアは同じ曲線に重なるので▶を押さなくても新着が分かる・15 分で止まる）。
矢じりは机の手前で止める（机の下に隠れない。隠した部屋のドア宛はそのまま）。
🎨 絵のテーマの優先順（S22）: sprite_track_themes（トラック名）＞ sprite_tenant_themes（部屋名）＞ sprite_theme（全体）。部屋単位が要るのは
workspace_hints の無い ws（副業）で名無しトラックが出るとトラック名キーでは追いつかないため。

更新の仕組みは daily_view と同じ: 中身が変わった時だけ office.html を書き、ページは office_stamp.js を
数秒おきに読んで差分があればリロードする（スクロール・選択は保たない＝配置図なので不要）。
"""
import base64
import hashlib
import html as html_mod
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .daily_view import load_board
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
PROPS_DIR = Path(__file__).resolve().parent.parent / "assets" / "props"
PROP_NAMES = ("sofa", "table", "plant", "coffee", "vending", "printer")  # 休憩室の小物（S22）。表示箱の大きさは assets/props/README.md


def load_props(props_dir: Path = PROPS_DIR) -> dict:
    """assets/props/<name>.png|svg（休憩室の小物）を data URI で返す。無い物はコード内の矩形描画。仕様=同フォルダ README。"""
    out = {}
    for name in PROP_NAMES:
        for ext, mime in (("png", "image/png"), ("svg", "image/svg+xml")):
            f = props_dir / f"{name}.{ext}"
            if f.exists():
                try:
                    out[name] = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()
                except OSError:
                    pass
                break
    return out


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


def _hub_side(order, cfg: dict) -> list:
    """cfg.office.hub_side（トラック名リスト）→ ハブの右隣に小机で並べるトラック名（order にある名前へ正規化・S22）。"""
    by_norm = {_norm(t["name"]): t["name"] for t in order}
    out = []
    for m in (cfg.get("office") or {}).get("hub_side") or []:
        n = by_norm.get(_norm(m))
        if n and n not in out:
            out.append(n)
    return out


def away_cutoff(cfg: dict) -> float:
    """💤 離席中の下限（epoch）。稼働プロセスは無いがこの時刻以降に jsonl が動いた席を「離席中」とみなす
    （S23・ふくちゃん「当日中はずっと」）。office.away_since: "today"（既定）＝今日 0:00 ローカル／数値＝その時間ぶん前。
    日付が変わると前日分は自動で消える（mtime 由来なので stamp は安定＝無駄な再読込なし）。"""
    v = (cfg.get("office") or {}).get("away_since", "today")
    now = datetime.now()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return now.timestamp() - float(v) * 3600
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


# 🏠 退社（S23 ⑩）: 人間の発話の文頭「退社」。単独か、後ろが空白・句読点・「します」等だけ拾う（「退社表示…」のような熟語は拾わない）
LEAVE_RE = re.compile(r"^退社(?:$|(?=[\s。．！!、,，・（(「]|します|する|です|だ|ね|よ|〜))")
_RET_DATE_RE = re.compile(r"次\s*(\d{1,2})[/／](\d{1,2})")  # 復帰日の明示「次 10/20」
_RET_WD_RE = re.compile(r"次\s*([月火水木金土日])曜?")  # 「次 月曜」
_WD = "月火水木金土日"


def leave_of(text: str, ts: float, now: float | None = None):
    """🏠 退社（S23 ⑩）: 発話 text（人間ターン・時刻 ts）が退社の締めなら {ts, t, until, until_t, day} を返す。
    復帰日＝「次 10/20」「次 月曜」があればそれ・無ければ次の平日（土日を飛ばす・祝日は見ない）。until（復帰日 0:00）を
    過ぎていれば None＝自動で解ける。ふくちゃんの例「PTA対応は今日の作業完了・月曜まで作業なし→離席中とは別に退社」。"""
    text = (text or "").strip()
    if not LEAVE_RE.match(text):
        return None
    d0 = datetime.fromtimestamp(ts)
    day0 = d0.replace(hour=0, minute=0, second=0, microsecond=0)
    ret = None
    m = _RET_DATE_RE.search(text)
    if m:
        try:
            ret = day0.replace(month=int(m.group(1)), day=int(m.group(2)))
            if ret <= day0:
                ret = ret.replace(year=ret.year + 1)
        except ValueError:
            ret = None
    if ret is None:
        m2 = _RET_WD_RE.search(text)
        want = _WD.index(m2.group(1)) if m2 else None
        ret = day0 + timedelta(days=1)
        while (ret.weekday() != want) if want is not None else (ret.weekday() >= 5):
            ret += timedelta(days=1)
    until = ret.timestamp()
    if (datetime.now().timestamp() if now is None else now) >= until:
        return None
    return {"ts": int(ts), "t": d0.strftime("%H:%M"), "day": d0.strftime("%m/%d"),
            "until": until, "until_t": ret.strftime("%m/%d")}


def last_human_turns(humans) -> dict:
    """sid → 最後の人間ターン（messages.collect の humans から・S23 ⑩）。"""
    last: dict = {}
    for h in humans or []:
        sid = h.get("sid")
        if sid and (sid not in last or h["ts"] >= last[sid]["ts"]):
            last[sid] = h
    return last


def leave_sids(humans, now: float | None = None) -> dict:
    """sid → 🏠 退社情報。各セッションの最後の人間ターンだけを見る（あとで何か打てば自動で解ける・S23 ⑩）。"""
    out = {}
    for sid, h in last_human_turns(humans).items():
        lv = leave_of(h.get("text"), h["ts"], now)
        if lv:
            out[sid] = dict(lv, sid=sid, text=(h.get("text") or "")[:60])
    return out


def _lounge(cfg: dict):
    """cfg.office.lounge（dict か true）→ {name, slot, break_after_min}。無ければ None＝休憩室なし（S22）。"""
    lg = (cfg.get("office") or {}).get("lounge")
    if not lg:
        return None
    if not isinstance(lg, dict):
        lg = {}
    try:
        mins = int(lg.get("break_after_min", 20))
    except (TypeError, ValueError):
        mins = 20
    return {"name": str(lg.get("name") or "☕ 休憩室"), "slot": str(lg.get("slot") or "tr"), "break_after_min": max(1, mins)}


def _board(cfg: dict):
    """🧾 休憩室のホワイトボードに書く今日の daily の要点（S23）。daily_view が無い／読めない時は None＝S22 の小さい板。"""
    try:
        return load_board(cfg)
    except Exception:  # daily の書式ゆれで配置図全体を落とさない
        return None


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
    side = {_norm(m) for m in (office.get("hub_side") or [])}  # 🪑 ハブの隣に置くトラックは島に入れない（S22）
    names = [t["name"] for t in order if _norm(t["name"]) not in side]
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
                     "rc": bool((registry.get(sid) or {}).get("bridgeSessionId")),  # 📱 Remote Control オン（キーの有無＝ハブ 10/15 発見・オフ時は消える/None 想定）
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
        """席名: 案件名 → 未分類。/rename 名（nameSource=user）は mapping.py ⓪ で最優先に案件名へ正規化済み
        （S23・相棒セッション「絵の作成」は名前そのままが案件名になる）なのでここでは見ない。
        本業以外の部屋の未分類席はワークスペース名で出す（部屋をまたいで「（未分類）」1席に溶けないため）。"""
        p = proj(sid)
        if p:
            return p
        lv = live.get(sid) or {}
        if tc and tenant_of(sid) != tc["main"]:
            w = (labels.get(sid) or {}).get("ws") or lv.get("ws")
            if w:
                return f"（{w}）"
        return "（未分類）"

    def seat_or_none(sid):
        return seat(sid) if (sid in labels or sid in live) else None

    recent, cut = res.get("recent") or {}, away_cutoff(cfg)  # 💤 離席中の材料（S23）
    last_turn, leaves = last_human_turns(res.get("humans")), leave_sids(res.get("humans"))  # 🏠 退社の材料（S23 ⑩）
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
        # 💤 離席中（S23）: 稼働プロセスは無いが当日（away_cutoff 以降）に jsonl が動いたセッション・新しい順。ハブも含む（pop の前）
        t["away"] = [] if t["live"] else sorted(
            [{"sid": x["sid"], "s": x["s"], "ts": int(recent[x["sid"]]),
              "t": datetime.fromtimestamp(recent[x["sid"]]).strftime("%H:%M"),
              "name": (labels.get(x["sid"]) or {}).get("name") or "",
              "model": (labels.get(x["sid"]) or {}).get("model") or ""}
             for x in t["sessions"] if recent.get(x["sid"], 0) >= cut],
            key=lambda a: -a["ts"])
        # 🏠 退社（S23 ⑩）: この席で「最後に人間が打った」のが最新のセッションが文頭「退社」で締められ、復帰日前なら退社。
        # 作業中・許可待ちの稼働があれば在席が勝つ（締めの台帳同期が終わるまで）。待機だけの稼働は帰った扱いで席から下げる
        # （窓を閉じ忘れても席は退社）。離席中よりも退社が勝つ
        newest = max((x["sid"] for x in t["sessions"] if x["sid"] in last_turn),
                     key=lambda s: last_turn[s]["ts"], default=None)
        lv = leaves.get(newest) if newest else None
        if lv and not any((x["live"] or {}).get("status") in ("busy", "perm") for x in t["occupied_by"]):
            lb, lvl = labels.get(newest) or {}, live.get(newest) or {}
            t["leave"] = dict(lv, s=lb.get("s"), name=lvl.get("name") or lb.get("name") or "",
                              model=lvl.get("model") or lb.get("model") or "")
            t["away"], t["occupied_by"], t["live"] = [], [], False
        t["cur"] = t["sessions"][0] if t["sessions"] else None
        # 席の部屋＝いちばん新しい（稼働優先）セッションの部屋。同じ案件を別 ws で開いても席は1つ
        t["tenant"] = tenant_of(t["cur"]["sid"]) if (tc and t["cur"]) else (tc["main"] if tc else None)
    hub = tracks.pop(hub_disp, {"name": hub_disp, "sessions": [], "occupied_by": [],
                                "live": False, "away": [], "cur": None})
    order = sorted(tracks.values(), key=lambda t: (not t["live"], not t["away"], not t.get("leave"), t["name"]))  # 稼働 → 💤 離席中 → 🏠 退社 → 空席

    # 止まっているトラックも空席で出す（10/15 ふくちゃん）: cfg.office.teams に載っている名前のうち
    # 時間幅内にセッションが無いものを sessions=[] の席として足す（島わけで席が固定され、空席＝止まって
    # いるトラックが見える）。既定 on・office.show_idle_tracks=false で従来どおり稼働分だけ。
    office_cfg = cfg.get("office") or {}
    if office_cfg.get("show_idle_tracks", True):
        have = {_norm(t["name"]) for t in order} | {_norm(hub_disp)}
        for members in list((office_cfg.get("teams") or {}).values()) + [office_cfg.get("hub_side") or []]:
            for m in members or []:
                if _norm(m) in have:
                    continue
                have.add(_norm(m))
                order.append({"name": m, "sessions": [], "occupied_by": [], "live": False, "away": [],
                              "cur": None, "dormant": True, "tenant": tc["main"] if tc else None})

    # 人間の位置＝稼働セッションのうち「人間の最終発話」が最新の1件の席の横（S21・ハブ案）。
    # 複数ウィンドウで同時に打っていても最新1件で割り切る。注意の所在の指標なので、話しかけて
    # いないのに busy な席＝勝手に進んでいるエージェントが浮き上がる。表示名は cfg.office.human。
    human = None
    for t in [hub] + order:
        for x in t["sessions"]:  # occupied_by でなく sessions＝🏠 退社で席から下げた待機セッションも材料に（S23 ⑩）
            lh = (x["live"] or {}).get("last_human")
            if lh and (human is None or lh["ts"] > human["ts"]):
                human = {"track": t["name"], "sid": x["sid"], "s": x["s"], "ts": lh["ts"],
                         "t": datetime.fromtimestamp(lh["ts"]).strftime("%H:%M"), "text": lh["text"]}
    # 🏠 人間の最後の発話が「退社」なら 👤 も帰宅扱い＝置かない（休憩室に居続けるのは変。次に何か打てば戻る・S23 ⑩）
    newest_leave = max((lv["ts"] for lv in leaves.values()), default=None)
    if human and newest_leave is not None and newest_leave >= int(human["ts"]):
        human = None
    hname = (cfg.get("office") or {}).get("human", "人間")
    if human:
        human["name"] = hname
    # 👤 再生にふくちゃん（S23）: 今日（away_cutoff 以降）の人間ターンを席の移動列に。同じ席が続く分は 1 つに畳み（n＝回数）、
    # break_after_min 以上の空きは休憩室経由（lounge: true）。JS 側で 📨 の流れと時刻順に混ぜて ▶ 再生する
    lounge = _lounge(cfg)
    brk = (lounge["break_after_min"] if lounge else 0) * 60
    moves, prev_ts = [], None
    for h in sorted(res.get("humans") or [], key=lambda h: h["ts"]):
        tr = seat_or_none(h["sid"]) if h["ts"] >= cut else None
        if not tr:
            continue
        hts = int(h["ts"])
        if brk and prev_ts is not None and hts - prev_ts >= brk:
            moves.append({"lounge": True, "ts": prev_ts + brk,
                          "t": datetime.fromtimestamp(prev_ts + brk).strftime("%H:%M")})
        if moves and not moves[-1].get("lounge") and moves[-1]["track"] == tr:
            moves[-1]["n"] += 1
        else:
            moves.append({"ts": hts, "t": datetime.fromtimestamp(hts).strftime("%H:%M"), "track": tr,
                          "s": (labels.get(h["sid"]) or {}).get("s"), "text": " ".join((h["text"] or "").split()), "n": 1})
        prev_ts = hts

    edges = []
    for e in res.get("edges") or []:
        fp, tp = seat_or_none(e["frm"]), seat_or_none(e["to"])
        edges.append({
            "ts": e["ts"], "t": _fmt_local(e["ts"]), "day": _fmt_local(e["ts"])[:5],
            "frm": fp or "（未分類）", "to": tp or (e.get("to_label") or "？"),
            "frm_s": (labels.get(e["frm"]) or {}).get("s"), "to_s": (labels.get(e["to"]) or {}).get("s"),
            "to_known": tp is not None,
            "summary": e.get("summary") or e.get("preview") or "", "preview": e.get("preview") or "",
            "body": (e.get("body") or "")[:1500],  # 📨 本文（ログの行クリックで開く・席の吹き出し・S24）
            "ok": e.get("ok", True), "read": e.get("read"), "removed": bool(e.get("removed")),
        })
    unread = sum(1 for e in edges if e["ok"] and not e["removed"] and e["read"] is False)
    failed = sum(1 for e in edges if not e["ok"])

    # 部屋（S22）: 最初の部屋＝本業（ハブ＋島わけ）・残りは席の一覧。設定済みの部屋は空室でも出す・
    # other（どこにも無い ws）は席がある時だけ出す。部屋が未設定なら従来どおり islands だけ
    rooms = []
    islands = build_islands(order, cfg)
    hub_side = _hub_side(order, cfg)
    if tc:
        main_order = [t for t in order if t["tenant"] == tc["main"]]
        islands = build_islands(main_order, cfg)
        hub_side = _hub_side(main_order, cfg)
        rooms.append({"name": tc["main"], "main": True, "islands": islands})
        for n in tc["names"][1:] + [tc["other"]]:
            ts = [t["name"] for t in order if t["tenant"] == n]
            if ts or n != tc["other"]:
                rooms.append({"name": n, "main": False, "tracks": ts})
    return {"hub": hub, "tracks": order, "edges": edges, "human": human, "sprites": load_sprites(),
            "human_moves": moves, "human_name": hname, "replay_from": cut,  # 👤 今日の席移動と再生の始点（▶ 再生用・S23）
            "sprite_themes": load_sprite_themes(),
            "sprite_default": (cfg.get("office") or {}).get("sprite_theme", ""),  # 既定テーマ（プルダウン未選択時）
            "sprite_variants": (cfg.get("office") or {}).get("sprite_variants") or {},  # トラック名→種類名の固定割当（任意）
            "sprite_track_themes": (cfg.get("office") or {}).get("sprite_track_themes") or {},  # トラック名→テーマ名（席ごとの別テーマ・例: 業務ハブだけ ninja）
            "sprite_tenant_themes": (cfg.get("office") or {}).get("sprite_tenant_themes") or {},  # 部屋名→テーマ名（副業の部屋ごとに別の絵・S22）
            "islands": islands, "rooms": rooms, "hub_side": hub_side,  # 🪑 ハブの右隣の小机（S22）
            "live_near_hub": bool((cfg.get("office") or {}).get("live_near_hub", False)),  # 🧲 稼働席をハブ側に詰める既定（S22）
            "lounge": _lounge(cfg), "props": load_props(),  # ☕ 休憩室（S22・無ければ None）と小物の絵（assets/props/）
            "board": _board(cfg),  # 🧾 ホワイトボードの要点（S23・daily から）
            "counts": {"edges": len(edges), "unread": unread, "failed": failed,
                       "live": sum(len(t["occupied_by"]) for t in order) + len(hub["occupied_by"]),
                       "away": sum(1 for t in [hub] + order if not t["live"] and t.get("away")),  # 💤 離席中の席数（S23）
                       "leave": sum(1 for t in [hub] + order if t.get("leave"))},  # 🏠 退社の席数（S23 ⑩）
            "window_hours": (cfg.get("office") or {}).get("window_hours", 48)}


CSS = """
*{box-sizing:border-box}
:root{color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
  --border:rgba(255,255,255,.10);--wash:rgba(255,255,255,.055);--gold:#f5c542;--cyan:#5ad1e6;--mag:#e66fd1;--red:#e5533b;--scene:#1b1d2a}
:root[data-theme="light"]{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --border:rgba(11,11,11,.10);--wash:rgba(11,11,11,.045);--gold:#b8860b;--cyan:#1d8fa6;--mag:#b3399e;--red:#c2341f;--scene:#e9e5dc}
html,body{height:100%} body{margin:0;height:100vh;overflow:hidden;display:flex;flex-direction:column;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;font-size:13px;line-height:1.5}
header{flex:none;padding:10px 16px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:600} .sub{color:var(--muted);font-size:11.5px}
.navs{margin-left:auto;display:flex;gap:6px;align-items:center}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;padding:3px 11px;font-size:12px;cursor:pointer;text-decoration:none;font-family:inherit}
.tgl:hover{background:var(--wash);color:var(--ink)} .tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
.wsb{padding:1px 8px;font-size:11px;margin-left:3px} .tgl.on{background:var(--wash);color:var(--ink);border-color:var(--ink2)}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;grid-template-rows:minmax(0,1fr);flex:1 1 0;min-height:0;overflow:hidden}
#scene{overflow:hidden;background:var(--scene);min-height:0;height:100%} #scene svg{display:block;width:100%;height:100%;shape-rendering:crispEdges}
aside{background:var(--surface);border-left:1px solid var(--border);overflow-y:auto;min-height:0;height:100%;padding:10px 12px}
aside h2{position:sticky;top:-10px;background:var(--surface);padding:6px 0;z-index:1}
aside h2{font-size:12.5px;margin:4px 0 8px;color:var(--muted);font-weight:600;display:flex;align-items:center;gap:8px} aside h2 .tgl{padding:1px 8px;font-size:11px;white-space:nowrap} aside h2 .tgl:first-of-type{margin-left:auto}
#spr{font:inherit;font-size:11px;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:1px 4px}
.day{color:var(--muted);font-size:11px;margin:10px 0 2px;border-bottom:1px solid var(--border)}
.row{padding:5px 6px;border-left:3px solid transparent;cursor:pointer;line-height:1.35;border-radius:0 6px 6px 0}
.row:hover,.row.hl{background:var(--wash);border-left-color:var(--gold)}
.row .t{color:var(--muted);margin-right:6px;font-variant-numeric:tabular-nums}.row .who{color:var(--cyan)}
.row .un{color:var(--gold);font-weight:600}.row .ng{color:var(--red);font-weight:600}.row .rm{color:var(--muted)}
.row .sm{color:var(--ink2)} .row .mbd{display:none;white-space:pre-wrap;word-break:break-word;margin-top:4px;padding:6px 8px;border-radius:6px;background:var(--wash);color:var(--ink);font-size:12px;max-height:320px;overflow:auto;cursor:text} .row.open .mbd{display:block}
.row .mor{color:var(--muted);font-size:11px;margin-left:4px}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--ink2)}
.sw{display:inline-block;width:10px;height:10px;margin-right:4px;vertical-align:middle;border-radius:2px}
.tiles{display:flex;gap:8px} .tile{border:1px solid var(--border);border-radius:8px;padding:2px 10px;font-size:12px;color:var(--ink2)}
.tile b{color:var(--ink);font-size:14px;margin-right:3px} .tile.warn b{color:var(--gold)} .tile.bad b{color:var(--red)}
.edge{fill:none;stroke-width:3;cursor:pointer} .edge.hl{stroke-width:5;filter:drop-shadow(0 0 4px #fff)} :root[data-theme="light"] .edge.hl{filter:drop-shadow(0 0 3px rgba(0,0,0,.45))}
.edge.ng{stroke-dasharray:6 5} .edge.rm{stroke-dasharray:2 4} .flow{fill:none;stroke:#fff;stroke-width:2;stroke-dasharray:6 11;animation:flow 1s linear infinite;pointer-events:none} @keyframes flow{to{stroke-dashoffset:-17}}
@keyframes blink{50%{opacity:0}} .cur{animation:blink 1s step-end infinite}
@keyframes type{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}} .typing{animation:type .5s steps(2) infinite}
@keyframes alert{50%{opacity:.35}} .perm{animation:alert .8s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}} .human{animation:bob 1.6s ease-in-out infinite}
@keyframes steam{0%{opacity:.8;transform:translateY(0)}100%{opacity:0;transform:translateY(-12px)}} .steam{animation:steam 1.8s ease-out infinite;transform-box:fill-box} .steam.s2{animation-delay:.7s}
@keyframes paper{0%{transform:translateX(-26px);opacity:0}25%{opacity:1}100%{transform:translateX(14px);opacity:0}} .paper{animation:paper 2.2s linear infinite;transform-box:fill-box}
@keyframes wink{0%,100%{opacity:1}50%{opacity:.25}} .vend{animation:wink 2.4s ease-in-out infinite}
@keyframes zz{0%{opacity:.3;transform:translateY(0)}50%{opacity:1}100%{opacity:.3;transform:translateY(-6px)}} .zz{animation:zz 2.4s ease-in-out infinite;transform-box:fill-box}
.empty{padding:30px;color:var(--muted)}
footer{flex:none;padding:6px 16px;color:var(--muted);font-size:11px;border-top:1px solid var(--border)}
#rbar{display:none;flex-basis:100%;flex-wrap:wrap;align-items:center;gap:4px 12px;min-width:0} header.playing #rbar{display:flex} header.playing .legend{display:none}
#rclk{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--gold);white-space:nowrap;line-height:1.2;min-width:3.2em} #rcap{flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
#rcnt{color:var(--muted);font-size:11.5px;white-space:nowrap;font-variant-numeric:tabular-nums} #rtrk{flex-basis:100%;height:3px;background:var(--wash);border-radius:2px;overflow:hidden} #rprog{height:100%;width:0;background:var(--gold);transition:width .3s linear}
"""

# テーマの優先順: ?theme=light|dark（保存しない・スクショ用）＞ 保存値 csv-theme（他ページと共通）＞ light（オフィスだけの既定・S22）
HEAD_JS = "try{var t=localStorage.getItem('csv-theme');try{var q=new URLSearchParams(location.search).get('theme');if(q==='light'||q==='dark')t=q;}catch(e){}document.documentElement.dataset.theme=t||'light';}catch(e){}"

# D / STAMP / POLL_MS / HUB はサーバ側で埋める
PAGE_JS = r"""
(function(){
var root=document.documentElement, btn=document.getElementById('tgl');
function lab(){btn.textContent=root.dataset.theme==='light'?'☾ ダーク':'☀ ライト';} lab();
btn.onclick=function(){root.dataset.theme=root.dataset.theme==='light'?'dark':'light';try{localStorage.setItem('csv-theme',root.dataset.theme);}catch(e){} lab();
 var u=new URL(location.href);if(u.searchParams.has('theme')){u.searchParams.delete('theme');location.href=u.toString();}else location.reload();};  // 図の配色は読み込み時に決めるので再読込（?theme= の一時上書きは外す）
var clk=document.getElementById('clock'), WD='日月火水木金土', skyFn=null;
function tick(){var d=new Date(),p=function(n){return('0'+n).slice(-2);};clk.textContent=(d.getMonth()+1)+'/'+d.getDate()+'（'+WD[d.getDay()]+'） '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());if(skyFn)skyFn(d);}
tick(); setInterval(tick,1000);
// 再読込は ▶ 再生中なら保留して再生の終わりに回す（10 秒ごとの stamp・イベント境界・休憩しきい値の全部・S23）
function softReload(){if(window.csvPlaying){window.csvReloadAfter=true;return;}location.reload();}
window.__officeStamp=function(s){if(s&&s!==STAMP)softReload();};
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
var MODELS={opus:1,sonnet:1,haiku:1,fable:1,unknown:1}, POSES={typing:1,idle:1,wait:1,rest:1};  // rest＝💤 離席中（絵が無ければ idle 系に落ちる・S23）
function varsOf(M){if(M.__vars)return M.__vars;var vs={};Object.keys(M).forEach(function(k){var t=k.split('-');if(t[0]!=='agent')return;t.slice(1).forEach(function(w){if(!MODELS[w]&&!POSES[w])vs[w]=1;});});return (M.__vars=Object.keys(vs).sort());}
var VOVR=D.sprite_variants||{}, TOVR=D.sprite_track_themes||{}, NOVR=D.sprite_tenant_themes||{};
var RESV={};Object.keys(VOVR).forEach(function(k){RESV[VOVR[k]]=1;});  // 役目の種類（hub・scribe 等）は名指しの席だけ＝ハッシュで他の席に配らない（S23 ⑦）
function variantFor(track,M){var ALL=varsOf(M||SP);if(!ALL.length)return '';if(VOVR[track]&&ALL.indexOf(VOVR[track])>=0)return VOVR[track];var VARS=ALL.filter(function(v){return !RESV[v];});if(!VARS.length)return '';var h=5381;for(var i=0;i<track.length;i++)h=((h*33)^track.charCodeAt(i))>>>0;return VARS[h%VARS.length];}
// 席ごとの別テーマ: トラック名（office.sprite_track_themes）＞部屋名（office.sprite_tenant_themes・trackRoom は部屋の配置時に埋まる）＞画面のテーマ。指定のテーマが無ければ次へ落ちる
function mapFor(track){var th=TOVR[track]||NOVR[trackRoom[track]];if(th){var tm=THEMES[String(th).split('/')[0]];if(tm)return tm;}return SP;}
function modelKey(model){return /opus/.test(model||'')?'opus':/sonnet/.test(model||'')?'sonnet':/haiku/.test(model||'')?'haiku':/fable/.test(model||'')?'fable':'unknown';}
function sprite(x,y,shirt,model,status,track){var M=mapFor(track||''),mk=modelKey(model),pose=status==='busy'?'typing':status==='perm'?'wait':status==='away'?'rest':'idle',v=variantFor(track||'',M);
 var direct=[],gray=[];
 if(v){direct.push('agent-'+v+'-'+mk+'-'+pose,'agent-'+v+'-'+mk);gray.push('agent-'+v+'-'+pose,'agent-'+v);}
 direct.push('agent-'+mk+'-'+pose,'agent-'+mk);gray.push('agent-'+pose,'agent');
 for(var i=0;i<direct.length;i++)if(M[direct[i]])return img(M[direct[i]],x,y,null);
 for(var j=0;j<gray.length;j++)if(M[gray[j]]){var id='tint'+shirt.replace('#','');tints[id]=shirt;return img(M[gray[j]],x,y,id);}
 var s='',sc=3;SPR.forEach(function(row,r){row.split('').forEach(function(c,i){if(c==='.')return;var col=(c==='T'||c==='A')?shirt:PAL[c];s+='<rect x="'+(x+i*sc)+'" y="'+(y+r*sc)+'" width="'+sc+'" height="'+sc+'" fill="'+col+'"/>';});});return s;}
var SCR={busy:'#39d353',idle:'#4aa3ff',perm:'#e5533b',empty:'#555'};
var unreadBy={}; D.edges.forEach(function(e){if(e.ok&&!e.removed&&e.read===false)unreadBy[e.to]=(unreadBy[e.to]||0)+1;});
// 🎨 図の配色（S22）: ページのテーマ（☀/☾）に追従。light＝明るいオフィス（白い壁・淡いタイル床・ベージュのカーペット・島名は濃い色）／dark＝従来
var LIGHT=document.documentElement.dataset.theme==='light';
function shade(c,f){var n=parseInt(c.slice(1),16);function k(v){return Math.max(0,Math.min(255,Math.round(v*f)));}return '#'+((1<<24)|(k(n>>16&255)<<16)|(k(n>>8&255)<<8)|k(n&255)).toString(16).slice(1);}
var PAL=LIGHT?{tileA:'#e6e2d9',tileB:'#ebe7df',grid:'#d8d3c8',carpetA:'#dccfb5',carpetB:'#d0c2a5',wall:'#fbfaf6',wallOp:1,wallStroke:'#8f887b',wallStrokeOp:0.9,wallW:6,roomTxt:'#1f2937',roomSub:'#4b5563',emptyTxt:'#6b7280',chair:'#5b6478',chair2:'#404859',plate:'#fffdf8',plateStroke:'#bfb8a8',islOp:0.18,sideOp:0.12,propTxt:'#374151',doorTxt:'#374151',doorStroke:'#6b7280',txt:function(c){return shade(c,0.66);}}
:{tileA:'#2a2d3f',tileB:'#2f3348',grid:null,carpetA:'#3d3330',carpetB:'#4a3f3a',wall:'#000',wallOp:0.16,wallStroke:'#cbd5e1',wallStrokeOp:0.5,wallW:4,roomTxt:'#e2e8f0',roomSub:'#cbd5e1',emptyTxt:'#94a3b8',chair:'#3a3e58',chair2:'#2a2d44',plate:'#f3efe0',plateStroke:null,islOp:0.10,sideOp:0.05,propTxt:'#cbd5e1',doorTxt:'#cbd5e1',doorStroke:'#cbd5e1',txt:function(c){return c;}};
function desk(x,y,t,big){var w=big?190:120,h=big?52:40,cur=t.cur,live=t.live,away=(!live&&t.away&&t.away.length)?t.away:null,onSofa=!!(away&&awayAt[t.name]>=0),leave=(!live&&t.leave)?t.leave:null;  // leave＝🏠 退社（S23 ⑩・Python 側で live/away は落としてある）
 var st='empty'; if(live){st=t.occupied_by.some(function(s){return s.live.status==='perm';})?'perm':t.occupied_by.some(function(s){return s.live.status==='busy';})?'busy':'idle';}
 var g='<g class="desk"'+(t.dormant?' opacity="'+(LIGHT?0.68:0.5)+'"':leave?' opacity="'+(LIGHT?0.85:0.75)+'"':'')+'>';  // 休眠席は薄く（ライトは背景が明るいぶん濃いめ）・🏠 退社は少し薄く
 var tip=t.name+(cur&&cur.s?' S'+cur.s:'')+'\n'+(live?t.occupied_by.map(function(s){return (s.live.status==='busy'?'🟢':s.live.status==='perm'?'🔴許可待ち':'🟡')+' '+(s.live.name||'')+' '+(s.live.model||'')+(s.s?' S'+s.s:'')+(s.live.rc?' 📱RC':'')+(s.live.title?'\n   '+s.live.title:'');}).join('\n'):leave?'🏠 退社（'+leave.t+' に締め・次の出勤 '+leave.until_t+'）\n   '+(leave.name||'')+' '+(leave.model||'')+(leave.s?' S'+leave.s:'')+(leave.text?'\n   「'+leave.text+'」':''):away?'💤 離席中（プロセスなし・最後の動き '+away[0].t+(onSofa?'・休憩室のソファ':'')+'）\n'+away.map(function(a){return '   '+(a.name||'')+' '+(a.model||'')+(a.s?' S'+a.s:'')+' 最後 '+a.t;}).join('\n'):t.dormant?'停止中（この時間幅にセッションなし）':'空席（稼働セッションなし）')+(t.sessions.length?'\nセッション履歴: '+t.sessions.map(function(s){return s.s?'S'+s.s:'S?';}).join(' '):'');
 g+='<title>'+esc(tip)+'</title>';
 if(away)g+='<rect x="'+(x-w/2-6)+'" y="'+(y-70)+'" width="'+(w+12)+'" height="'+(h+98)+'" rx="8" fill="#64748b" fill-opacity="0.08" stroke="#64748b" stroke-dasharray="6 4" stroke-width="2"/><text class="zz" x="'+(x+w/2-4)+'" y="'+(y-52)+'" text-anchor="end" font-size="14">💤</text>';  // 💤 離席中＝席は点線枠で確保（S23）
 if(leave)g+='<rect x="'+(x-w/2-6)+'" y="'+(y-70)+'" width="'+(w+12)+'" height="'+(h+98)+'" rx="8" fill="#a8a29e" fill-opacity="0.07" stroke="#a8a29e" stroke-dasharray="2 5" stroke-width="2"/><text x="'+x+'" y="'+(y-54)+'" text-anchor="middle" font-size="11" fill="'+PAL.emptyTxt+'">🏠 '+esc(leave.t)+' 退社</text><text x="'+x+'" y="'+(y-40)+'" text-anchor="middle" font-size="10" fill="'+PAL.emptyTxt+'">次 '+esc(leave.until_t)+'</text>';  // 🏠 退社＝主は不在（椅子だけ）・薄い点線枠と復帰日（S23 ⑩）
 if(live){t.occupied_by.slice(0,big?3:2).forEach(function(s,i){g+='<g class="'+(s.live.status==='busy'?'typing':s.live.status==='perm'?'perm':'')+'">'+sprite(x-w/2+14+i*44,y-62,MODEL_COL(s.live.model),s.live.model,s.live.status,t.name)+'</g>';});
  if(t.occupied_by.some(function(s){return s.live.rc;}))g+='<text x="'+(x+w/2-2)+'" y="'+(y-6)+'" text-anchor="end" font-size="15">📱</text>';}  // 📱 Remote Control オンの席（S22）
 else if(away&&!onSofa){g+='<g opacity="0.45">'+sprite(x-w/2+14,y-62,MODEL_COL(away[0].model),away[0].model,'away',t.name)+'</g>';}  // ソファから溢れた離席者は席に半透明で残す
 else{g+='<rect x="'+(x-14)+'" y="'+(y-30)+'" width="28" height="22" fill="'+PAL.chair+'"/><rect x="'+(x-14)+'" y="'+(y-8)+'" width="28" height="6" fill="'+PAL.chair2+'"/>';}
 g+='<rect x="'+(x-w/2)+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="#8b5a2b"/><rect x="'+(x-w/2)+'" y="'+y+'" width="'+w+'" height="8" fill="#b57a3e"/>';
 var nm=big?2:1;for(var i=0;i<nm;i++){var mx=x-(nm-1)*22+i*44-16;g+='<rect x="'+mx+'" y="'+(y+10)+'" width="32" height="22" fill="#111"/><rect x="'+(mx+2)+'" y="'+(y+12)+'" width="28" height="18" fill="'+SCR[st]+'"/>';if(st==='busy')g+='<rect class="cur" x="'+(mx+6)+'" y="'+(y+22)+'" width="4" height="4" fill="#fff"/>';}
 var label=t.name+(cur&&cur.s?' S'+cur.s:'');
 g+='<rect x="'+(x-w/2)+'" y="'+(y+h+4)+'" width="'+w+'" height="18" fill="'+PAL.plate+'"'+(PAL.plateStroke?' stroke="'+PAL.plateStroke+'"':'')+'/><text x="'+x+'" y="'+(y+h+17)+'" text-anchor="middle" font-size="'+(label.length>12?10:12)+'" fill="#222">'+esc(label)+'</text>';
 if(t.occupied_by.length>1)g+='<text x="'+(x+w/2-4)+'" y="'+(y+h+17)+'" text-anchor="end" font-size="10" fill="#777">+'+(t.occupied_by.length-1)+'</text>';
 var un=unreadBy[t.name]||0;
 if(un)g+='<rect x="'+(x+w/2-30)+'" y="'+(y-14)+'" width="30" height="14" fill="#f5c542"/><text x="'+(x+w/2-15)+'" y="'+(y-3)+'" text-anchor="middle" font-size="11" fill="#222">📨'+un+'</text>';
 return g+'</g>';}
var pos={}, floors='', walls='', door={}, trackRoom={}, hubVisible=true, LOUNGE=null, WIN=null, SOFA=null, awayAt={};  // SOFA＝💤 離席者の座る位置（drawLounge で埋まる）・awayAt＝席名→ソファ番号（-1＝溢れ）
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
var SIDE=D.hub_side||[];  // 🪑 ハブの右隣に小机で並べるトラック（島には入らない・S22）
function roomTracks(r){var ts=r.main?[HUB].concat(SIDE):(r.tracks||[]).slice();if(r.main)(r.islands||[]).forEach(function(i){ts=ts.concat(i.tracks);});return ts;}
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
// ---------- ☕ 休憩室と小物（S22）。assets/props/<名前>.png があれば絵・無ければ矩形で仮描き（箱の大きさは README） ----------
var PR=D.props||{};
function R(x,y,w,h,f,ex){return '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="'+f+'"'+(ex||'')+'/>';}
function prop(n,x,y,w,h){return PR[n]?'<image href="'+PR[n]+'" x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" preserveAspectRatio="xMidYMax meet" style="image-rendering:pixelated"/>':null;}
function plant(ox,oy,k){k=k||1;var im=prop('plant',ox,oy,36*k,70*k);if(im)return im;var s='';[[6,46,24,24,'#c2410c'],[4,44,28,6,'#ea580c'],[16,20,4,28,'#166534'],[2,24,14,10,'#22c55e'],[20,14,16,10,'#16a34a'],[6,8,12,10,'#4ade80'],[18,30,12,8,'#22c55e']].forEach(function(p){s+=R(ox+p[0]*k,oy+p[1]*k,p[2]*k,p[3]*k,p[4]);});return s;}
// 🧾 ホワイトボード（S23・ハブ提案 10/15）: 休憩室の壁いっぱいに今日の daily の要点＝🔴 TOP 見出し3行（未済→済の順）・⏰ 次の時刻固定イベント・⏸ 返信待ち・端に在席/未読/不達。
// 中身は D.board（daily_view.load_board）。クリックで 📓 日誌。幅か高さが足りない（島と同居で h=230 等）時は S22 の小さい板（日付・在席/未読・通数）
var BOARD=D.board||null, WBF=' font-family="Segoe Print,Yu Kyokasho N,UD Digi Kyokasho N-R,cursive"', WB={};
function twb(s,fs){var w=0;Array.from(s).forEach(function(ch){var c=ch.codePointAt(0);w+=c>0xffff?fs*1.15:c>=0x2e80?fs:c>0xff?fs*0.8:fs*0.62;});return w;}  // 文字幅の見積り（CJK=1em・絵文字=1.15em・英数=0.62em）
function fit(s,px,fs){if(twb(s,fs)<=px)return s;var o='',a=Array.from(s);for(var i=0;i<a.length;i++){if(twb(o+a[i]+'…',fs)>px)break;o+=a[i];}return o+'…';}
function hm2m(t){var m=/^(\d{1,2}):(\d{2})$/.exec(t||'');return m?(+m[1])*60+(+m[2]):null;}
// ⏰ の1行: 「いま 15:00〜17:00 出荷完了処理 ／ 次 17:00 DotB 注文連携（あと1時間20分） → 19:00 定時」。until＝次に描き直す時刻（分）
function evLine(){if(!BOARD||!BOARD.events||!BOARD.events.length)return null;var d=new Date(),now=d.getHours()*60+d.getMinutes(),cur=null,nxt=[];
 BOARD.events.forEach(function(e){var a=hm2m(e.hm),b=hm2m(e.to);if(a==null)return;if(a>now)nxt.push(e);else if(b!=null&&now<b)cur=e;});
 function lab(e){return e.hm+(e.to?'〜'+e.to:'')+' '+e.label;}
 var s='',until=null,alts;if(cur){s+='いま '+lab(cur)+' ／ ';until=hm2m(cur.to);}
 if(nxt.length){var m=hm2m(nxt[0].hm)-now,rem='（あと'+(m>=60?Math.floor(m/60)+'時間':'')+((m%60||m<60)?(m%60)+'分':'')+'）',n2=nxt[1]?' → '+lab(nxt[1]):'';s+='次 '+lab(nxt[0]);alts=['⏰ '+s+rem+n2,'⏰ '+s+rem,'⏰ '+s];s+=rem+n2;until=until==null?hm2m(nxt[0].hm):Math.min(until,hm2m(nxt[0].hm));}
 else {s+=cur?'このあとの時刻固定はなし':'今日の時刻固定イベントは終了';alts=['⏰ '+s];}
 return {text:'⏰ '+s,alts:alts,until:until};}
function pick(alts,px,fs){for(var i=0;i<alts.length;i++)if(twb(alts[i],fs)<=px)return alts[i];return fit(alts[alts.length-1],px,fs);}  // 収まる最初の候補・全滅なら最短を … で切る
function whiteboard(bx,by,bw,bhMax){var c=D.counts,d=new Date(),date=(d.getMonth()+1)+'/'+d.getDate()+'（'+WD[d.getDay()]+'）',s='';
 function board(W,Hh){return R(bx,by,W,Hh,'#e5e7eb',' stroke="#9ca3af" stroke-width="2"')+R(bx+4,by+4,W-8,Hh-14,'#fff')+R(bx,by+Hh,W,5,'#9ca3af')+R(bx+8,by+8,6,6,'#ef4444')+R(bx+W-14,by+8,6,6,'#3b82f6');}
 if(!BOARD||bw<320||bhMax<120){if(bw<220)return '';var F=WBF+' font-size="12"';  // S22 の小さい板
  s+=board(220,80)+'<text x="'+(bx+14)+'" y="'+(by+26)+'"'+F+' fill="#1d4ed8">'+date+'</text>';
  s+='<text x="'+(bx+14)+'" y="'+(by+44)+'"'+F+' fill="#111">在席 '+c.live+'人 ／ 未読 <tspan fill="'+(c.unread?'#dc2626':'#111')+'">'+c.unread+'</tspan>通</text>';
  s+='<text x="'+(bx+14)+'" y="'+(by+62)+'"'+F+' fill="#111">📨 '+c.edges+'通 /'+D.window_hours+'h'+(c.failed?' ／ <tspan fill="#dc2626">不達 '+c.failed+'</tspan>':'')+'</text>';return s;}
 var fs=14,LH=19,maxw=bw-28,lines=[];  // 板の字は机名より大きく（画面全体が縮むので）
 if(BOARD.missing)lines.push({t:'📓 今日の日誌はまだ（業務ハブを起動すると作られる）',col:'#6b7280'});
 else{var top=BOARD.top||[],pend=top.filter(function(t){return !t.done;}),done=top.filter(function(t){return t.done;});
  pend.concat(done).slice(0,3).forEach(function(t){lines.push({t:(t.mark||'・')+' '+t.t,col:t.done?'#9ca3af':'#111',strike:t.done,tip:t.t});});
  if(!top.length)lines.push({t:'（🔴 TOP の見出しなし）',col:'#9ca3af'});
  var ev=evLine();if(ev)lines.push({t:ev.text,alts:ev.alts,col:'#b45309',id:'wbev'});
  var wt=BOARD.waiting;if(wt&&wt.n)lines.push({t:'⏸ 返信待ち '+wt.n+'件'+(wt.urgent?'（🔴'+wt.urgent+'）':'')+'：'+(wt.items||[]).join('・'),col:'#111',tip:'返信待ち（こちらボール外）\n'+(wt.items||[]).join('\n')});}
 var Hh=Math.min(bhMax,44+lines.length*LH+10),y0=by+24;
 s+='<a href="daily.html" target="_blank" rel="noopener" style="cursor:pointer"><title>📓 日誌を開く（daily.html）</title>'+board(bw,Hh);
 s+='<text x="'+(bx+14)+'" y="'+y0+'"'+WBF+' font-size="15" font-weight="700" fill="#1d4ed8">'+date+' 今日</text>';
 s+='<text x="'+(bx+bw-14)+'" y="'+y0+'" text-anchor="end"'+WBF+' font-size="13" fill="#111">在席 '+c.live+'人 ／ 未読 <tspan fill="'+(c.unread?'#dc2626':'#111')+'"'+(c.unread?' font-weight="700"':'')+'>'+c.unread+'</tspan>通 ／ 不達 <tspan fill="'+(c.failed?'#dc2626':'#111')+'"'+(c.failed?' font-weight="700"':'')+'>'+c.failed+'</tspan> ／ 📨 '+c.edges+'通/'+D.window_hours+'h</text>';
 s+=R(bx+12,y0+6,bw-24,2,'#1d4ed8',' fill-opacity=".45"');
 var y=y0+10+LH;lines.forEach(function(L){if(y>by+Hh-8)return;s+='<text'+(L.id?' id="'+L.id+'"':'')+' x="'+(bx+14)+'" y="'+y+'"'+WBF+' font-size="'+fs+'" fill="'+L.col+'"'+(L.strike?' text-decoration="line-through"':'')+'>'+(L.tip?'<title>'+esc(L.tip)+'</title>':'')+esc(L.alts?pick(L.alts,maxw,fs):fit(L.t,maxw,fs))+'</text>';y+=LH;});
 s+='</a>';WB={maxw:maxw,fs:fs};
 var ev0=evLine();if(ev0){setInterval(function(){var el=document.getElementById('wbev'),e=evLine();if(el&&e)el.textContent=pick(e.alts,WB.maxw,WB.fs);},30000);  // 「あと n分」は30秒ごとに書き直す
  if(ev0.until!=null){var left=(ev0.until-(d.getHours()*60+d.getMinutes()))*60000-d.getSeconds()*1000;if(left>0)setTimeout(softReload,Math.min(left+1500,2e9));}}  // 次のイベント境界で描き直し
 return s;}
function drawLounge(x,y,w,h,name){var col='#f59e0b',s='',fy=y+h-28,right=x+w-10;function fits(ox,iw){return ox+iw<=right;}
 s+='<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="12" fill="url(#carpet)" stroke="'+PAL.txt(col)+'" stroke-opacity="0.5" stroke-width="2"/><text x="'+(x+12)+'" y="'+(y+18)+'" font-size="13" font-weight="600" fill="'+PAL.txt(col)+'">'+esc(name)+'</text>';
 // 壁側: 🧾 ホワイトボードが壁いっぱい（S23 ⑤ 案A・窓は床のソファの隣に下ろした）。狭ければ S22 の小さい板
 // 🪟 窓（空は skyFn が時刻で塗る）: 床のソファの右・敷居は床線の 8px 上
 var wx=x+158,wy=fy-84; if(fits(wx-4,128)){WIN={x:wx+6,y:wy+6,w:108,h:58};
  s+=R(wx,wy,120,70,'#cbd5e1')+'<rect id="winsky" x="'+(wx+6)+'" y="'+(wy+6)+'" width="108" height="58" fill="#7dd3fc"/><g id="winstars" opacity="0">'+R(wx+20,wy+14,3,3,'#fff')+R(wx+50,wy+22,2,2,'#fff')+R(wx+90,wy+12,3,3,'#fff')+R(wx+70,wy+40,2,2,'#fff')+'</g><circle id="winsun" cx="'+(wx+40)+'" cy="'+(wy+26)+'" r="9" fill="#fde047"/>'+R(wx+58,wy+6,4,58,'#cbd5e1')+R(wx+6,wy+33,108,4,'#cbd5e1')+R(wx-4,wy+70,128,6,'#94a3b8');}
 var bx=x+24; s+=whiteboard(bx,y+34,right-bx,h-172);  // 板の高さ＝床側の自販機（104）に当たらない範囲
 // 床側（左→右）: 🛋️ ソファ・🪟 窓・☕ コーヒー（右隣が人の立ち位置）・🥤 自販機・🖨️ 印刷機。幅が足りなければ右から落とす（テーブル・鉢植えは ⑤ で落とした＝板の幅優先）
 var ox=x+24,im; if(fits(ox,120)){im=prop('sofa',ox,fy-56,120,56);s+=im||(R(ox,fy-52,120,24,'#3b5bdb',' rx="6"')+R(ox,fy-28,120,20,'#4c6ef5')+R(ox,fy-42,14,34,'#364fc7')+R(ox+106,fy-42,14,34,'#364fc7')+R(ox+59,fy-28,2,20,'#364fc7')+R(ox+10,fy-8,8,8,'#222')+R(ox+102,fy-8,8,8,'#222'));SOFA=[[ox+4,fy-56],[ox+42,fy-56],[ox+80,fy-56]];}  // 🛋️ 離席者の座る位置（最大3人・S23）
 ox=x+292; if(fits(ox,90)){im=prop('coffee',ox,fy-80,90,80);s+=im||(R(ox,fy-26,90,26,'#6b4423')+R(ox,fy-30,90,6,'#8b5a2b')+R(ox+20,fy-74,44,44,'#2d2d2d',' rx="3"')+R(ox+20,fy-78,44,6,'#444')+R(ox+24,fy-68,36,10,'#111')+R(ox+28,fy-65,4,4,'#39d353',' class="cur"')+R(ox+38,fy-52,8,6,'#555')+R(ox+36,fy-42,12,10,'#fff'));
  s+=R(ox+38,fy-48,3,5,'#ddd',' class="steam"')+R(ox+44,fy-48,3,5,'#ddd',' class="steam s2"'); LOUNGE={spot:[ox+96,fy-48],name:name};}
 ox=x+432; if(fits(ox,60)){im=prop('vending',ox,fy-104,60,104);if(im)s+=im;else{s+=R(ox,fy-104,60,100,'#dc2626',' rx="3"')+R(ox,fy-104,60,8,'#991b1b')+R(ox+6,fy-94,36,50,'#1e293b');
  [[0,'#f59e0b','#3b82f6','#22c55e'],[16,'#22c55e','#f59e0b','#e66fd1'],[32,'#3b82f6','#ef4444','#f59e0b']].forEach(function(r){for(var i=0;i<3;i++)s+=R(ox+10+i*12,fy-88+r[0],8,12,r[i+1]);});
  s+=R(ox+46,fy-92,10,40,'#111')+R(ox+48,fy-88,6,4,'#5ad1e6',' class="vend"')+R(ox+6,fy-34,36,14,'#111')+R(ox+4,fy-4,8,4,'#222')+R(ox+48,fy-4,8,4,'#222');}}
 ox=x+502; if(fits(ox,176)){var busy=[D.hub].concat(D.tracks).some(function(t){return (t.occupied_by||[]).some(function(q){return q.live&&q.live.status==='busy';});});
  im=prop('printer',ox,fy-100,176,100);s+=im||(R(ox+6,fy-16,128,14,'#4b5563')+R(ox+10,fy-2,10,4,'#222')+R(ox+120,fy-2,10,4,'#222')+R(ox,fy-76,140,60,'#9ca3af',' rx="4"')+R(ox,fy-80,140,6,'#d1d5db')+R(ox+10,fy-92,60,14,'#6b7280')+R(ox+12,fy-96,56,4,'#fff')+R(ox+100,fy-94,34,22,'#111',' rx="2"')+R(ox+103,fy-91,28,16,busy?'#39d353':'#4aa3ff')+R(ox+140,fy-46,36,8,'#6b7280')+R(ox+10,fy-60,120,4,'#6b7280'));
  if(busy)s+=R(ox+146,fy-54,24,6,'#fff',' class="paper"');  // 作業中の席があれば用紙が出続ける
  s+='<text x="'+(ox+70)+'" y="'+(fy+14)+'" text-anchor="middle" font-size="10" fill="'+PAL.propTxt+'" fill-opacity=".8">🖨️ 印刷機'+(busy?'（稼働中）':'')+'</text>';}
 return s;}
// 島レイアウト: 3×3 のセル。中央＝ハブ。島は config の順に r,l,t,b,tr,tl,br,bl のセルへ（slot 指定があれば優先）。
// 同じセルに複数の島が来たら縦に積む。セルの幅・高さは中の島の最大で決める＝重ならない。原点 (OX,OY) からの相対で組む
function layoutIslands(islands,OX,OY){
 var SLOT={r:[2,1],l:[0,1],t:[1,0],b:[1,2],tr:[2,0],tl:[0,0],br:[2,2],bl:[0,2]}, ORDER=['r','l','t','b','tr','tl','br','bl'];
 var cells={}; for(var cx0=0;cx0<3;cx0++)for(var cy0=0;cy0<3;cy0++)cells[cx0+','+cy0]=[];
 islands.forEach(function(isl,i){var sl=SLOT[isl.slot]||SLOT[ORDER[i%ORDER.length]];var m=isl.tracks.length,cols=Math.min(m,Math.max(Math.ceil(Math.sqrt(m)),Math.ceil(m/2))),rows=Math.ceil(m/cols);
  isl.cols=cols;isl.rows=rows;isl.w=(cols-1)*PX+120+PAD*2+30;isl.h=rows*PY-11+PAD*2+TITLE;cells[sl[0]+','+sl[1]].push(isl);});
 var LG=D.lounge?{lounge:true,name:D.lounge.name,w:420,h:230}:null; if(LG){var ls=SLOT[D.lounge.slot]||SLOT.tr;cells[ls[0]+','+ls[1]].push(LG);}  // ☕ 休憩室＝島の空きセル（既定 tr）。島と同居なら縦に積む
 var NS=SIDE.length, SHIFT=NS?65+(NS-1)*70:0, HUBW=NS?390+(NS-1)*140:260, HUBH=200, colW=[0,HUBW,0], rowH=[0,HUBH,0];  // 🪑 hub_side の小机の分だけ中央セルを広げ、ハブを左へずらす
 Object.keys(cells).forEach(function(k){var c=k.split(',').map(Number),L=cells[k];if(!L.length)return;var w=Math.max.apply(null,L.map(function(x){return x.w;})),h=L.reduce(function(a,x){return a+x.h;},0)+G*(L.length-1);
  colW[c[0]]=Math.max(colW[c[0]],w);rowH[c[1]]=Math.max(rowH[c[1]],h);});
 var colX=[OX,OX+colW[0]+(colW[0]?G:0)], rowY=[OY,OY+rowH[0]+(rowH[0]?G:0)]; colX[2]=colX[1]+colW[1]+G; rowY[2]=rowY[1]+rowH[1]+G;
 var hx=colX[1]+colW[1]/2-SHIFT, hy=rowY[1]+rowH[1]/2+30;  // ハブの中心（🧲 の距離の基準）
 SIDE.forEach(function(n,k){pos[n]=[Math.round(hx+165+k*140),Math.round(hy)];});  // 小机＝ハブ（幅190）の右 10px から 140px 間隔
 if(NS)floors+='<rect x="'+(hx-111)+'" y="'+(hy-82)+'" width="'+(352+(NS-1)*140)+'" height="166" rx="12" fill="#f5c542" fill-opacity="'+PAL.sideOp+'" stroke="'+PAL.txt('#f5c542')+'" stroke-opacity="0.45" stroke-dasharray="6 4" stroke-width="1.5"/>';  // ハブと小机をひとつの床で囲う
 if(LG)Object.keys(cells).forEach(function(k){var L=cells[k];if(L.length===1&&L[0]===LG){var c=k.split(',').map(Number);LG.w=colW[c[0]];LG.h=rowH[c[1]];}});  // セルを独占するなら目一杯に広げる
 Object.keys(cells).forEach(function(k){var c=k.split(',').map(Number),L=cells[k];if(!L.length)return;var tot=L.reduce(function(a,x){return a+x.h;},0)+G*(L.length-1);
  var y=c[1]===0?rowY[0]+rowH[0]-tot:c[1]===2?rowY[2]:rowY[1]+(rowH[1]-tot)/2;
  L.forEach(function(isl){var x=c[0]===0?colX[0]+colW[0]-isl.w:c[0]===2?colX[2]:colX[1]+(colW[1]-isl.w)/2;if(isl.lounge){floors+=drawLounge(x,y,isl.w,isl.h,isl.name);y+=isl.h+G;return;}var col=ISL_COL[islands.indexOf(isl)%ISL_COL.length];
   floors+='<rect x="'+x+'" y="'+y+'" width="'+isl.w+'" height="'+isl.h+'" rx="12" fill="'+col+'" fill-opacity="'+PAL.islOp+'" stroke="'+PAL.txt(col)+'" stroke-opacity="0.45" stroke-width="2"/><text x="'+(x+12)+'" y="'+(y+18)+'" font-size="13" font-weight="600" fill="'+PAL.txt(col)+'">'+esc(isl.name)+'</text><text x="'+(x+isl.w-10)+'" y="'+(y+18)+'" text-anchor="end" font-size="11" fill="'+PAL.txt(col)+'" fill-opacity="0.8">'+isl.tracks.length+'席</text>';
   floors+=plant(x+isl.w-36,y+isl.h-55,0.7);  // 🪴 島の角の観葉植物（飾り・配置計算には影響しない）
   var cellsOf=[];for(var k=0;k<isl.tracks.length;k++){var cc=k%isl.cols,rr=Math.floor(k/isl.cols);cellsOf.push([Math.round(x+PAD+60+cc*PX),Math.round(y+PAD+TITLE+62+rr*PY)]);}
   var names=isl.tracks.slice();if(MAGNET){cellsOf.sort(function(a,b){return Math.hypot(a[0]-hx,a[1]-hy)-Math.hypot(b[0]-hx,b[1]-hy);});names.sort(function(a,b){return liveRank(a)-liveRank(b);});}
   names.forEach(function(name,k){pos[name]=cellsOf[k];});
   y+=isl.h+G;});});
 return {w:(colW[2]?colX[2]+colW[2]:colX[1]+colW[1])-OX, h:(rowH[2]?rowY[2]+rowH[2]:rowY[1]+rowH[1])-OY, cx:hx, cy:hy};}
// 本業以外の部屋: 島なし・席を格子に並べるだけ
function layoutGrid(tracks,OX,OY){var m=tracks.length;if(!m)return {w:300,h:110,empty:true};var cols=Math.min(m,Math.max(2,Math.ceil(Math.sqrt(m)))),rows=Math.ceil(m/cols);
 tracks.forEach(function(name,k){var cc=k%cols,rr=Math.floor(k/cols);pos[name]=[Math.round(OX+PAD+60+cc*PX),Math.round(OY+PAD+62+rr*PY)];});
 return {w:(cols-1)*PX+120+PAD*2+30,h:rows*PY-11+PAD*2};}
// 部屋＝壁（太枠）＋名札（部屋名・席数・在席）。本業の部屋は島レイアウト・他は格子
function placeRoom(r,X,Y){var inner;
 if(r.main){inner=layoutIslands(r.islands||[],X+RP,Y+RT);CX=inner.cx;CY=inner.cy;hubVisible=true;}
 else inner=layoutGrid(r.tracks||[],X+RP,Y+RT);
 var w=Math.max(inner.w+RP*2,r.name.length*16+140), h=inner.h+RT+RP;
 walls+='<rect x="'+X+'" y="'+Y+'" width="'+w+'" height="'+h+'" rx="16" fill="'+PAL.wall+'" fill-opacity="'+PAL.wallOp+'" stroke="'+PAL.wallStroke+'" stroke-opacity="'+PAL.wallStrokeOp+'" stroke-width="'+PAL.wallW+'"/><text x="'+(X+16)+'" y="'+(Y+25)+'" font-size="16" font-weight="700" fill="'+PAL.roomTxt+'">'+esc(r.name)+'</text><text x="'+(X+w-14)+'" y="'+(Y+25)+'" text-anchor="end" font-size="12" fill="'+PAL.roomSub+'" fill-opacity="0.85">'+esc(roomCount(r))+'</text>';
 if(inner.empty)walls+='<text x="'+(X+w/2)+'" y="'+(Y+RT+62)+'" text-anchor="middle" font-size="13" fill="'+PAL.emptyTxt+'">空室（この時間幅にセッションなし）</text>';
 return {w:w,h:h};}
if(ROOMS.length){hubVisible=false;
 // 1つ目の部屋を左に・残りは右に縦積み（本業が横長なので）。絞り込み中は shown だけ
 var r0=placeRoom(shown[0],M,M), X2=M+r0.w+RG, ys=M, sideW=0;
 shown.slice(1).forEach(function(r){var s=placeRoom(r,X2,ys);sideW=Math.max(sideW,s.w);ys+=s.h+RG;});
 W=(sideW?X2+sideW:M+r0.w)+M; H=Math.max(M+r0.h,ys-RG)+M;
 // 絞り込みで隠した部屋＝右端のドア。そこ宛／そこ発の矢印は壁の外へ出る線になる
 if(hidden.length){var DX=W;W+=260;H=Math.max(H,M+40+hidden.length*110);
  hidden.forEach(function(r,k){var dp=[DX+230,M+80+k*110];door[r.name]=dp;
   walls+='<g><title>'+esc(r.name+'（絞り込みで非表示・ボタンで表示）')+'</title><rect x="'+(dp[0]-14)+'" y="'+(dp[1]-32)+'" width="28" height="64" rx="4" fill="#8b5a2b" stroke="'+PAL.doorStroke+'" stroke-opacity=".6" stroke-width="2"/><circle cx="'+(dp[0]+6)+'" cy="'+dp[1]+'" r="3" fill="#f5c542"/><text x="'+(dp[0]-22)+'" y="'+(dp[1]+4)+'" text-anchor="end" font-size="11" fill="'+PAL.doorTxt+'">🚪 '+esc(r.name)+'</text></g>';});}
}else if(D.islands&&D.islands.length){var r1=layoutIslands(D.islands,M,M);CX=r1.cx;CY=r1.cy;W=r1.w+M*2;H=r1.h+M*2;
}else{
 D.tracks.forEach(function(t,i){var a=-Math.PI/2+i*2*Math.PI/Math.max(n,1);pos[t.name]=[Math.round(CX+RX*Math.cos(a)),Math.round(CY+RY*Math.sin(a))];});
}
if(hubVisible)pos[HUB]=[CX,CY];
var svg='<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet"><defs><pattern id="tile" width="40" height="40" patternUnits="userSpaceOnUse"><rect width="40" height="40" fill="'+PAL.tileA+'"/><rect width="20" height="20" fill="'+PAL.tileB+'"/><rect x="20" y="20" width="20" height="20" fill="'+PAL.tileB+'"/>'+(PAL.grid?'<path d="M0,0.5 H40 M0.5,0 V40" stroke="'+PAL.grid+'" stroke-width="1" fill="none"/>':'')+'</pattern><pattern id="carpet" width="16" height="16" patternUnits="userSpaceOnUse"><rect width="16" height="16" fill="'+PAL.carpetA+'"/><rect x="2" y="2" width="4" height="4" fill="'+PAL.carpetB+'"/><rect x="10" y="10" width="4" height="4" fill="'+PAL.carpetB+'"/></pattern><marker id="ah" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker></defs><rect width="'+W+'" height="'+H+'" fill="url(#tile)"/>'+walls+floors;
var ts0=D.edges.length?Date.parse(D.edges[0].ts):0, ts1=D.edges.length?Date.parse(D.edges[D.edges.length-1].ts):1;
var NEW_MS=15*60000, fresh=[], flows='';  // 🔔 直近 15 分の矢印は流れる破線＝同じ席ペアは同じ曲線に重なるので動きで新着を見せる（S23）
D.edges.forEach(function(e,i){var A=pos[e.frm]||doorOf(e.frm),B=pos[e.to]||doorOf(e.to);if(!A||!B||(!pos[e.frm]&&!pos[e.to]))return;var x1=A[0],y1=A[1]+20,x2=B[0],y2=B[1]+20;
 var mx=(x1+x2)/2,my=(y1+y2)/2,dx=x2-x1,dy=y2-y1,L=Math.hypot(dx,dy)||1,ox=-dy/L*28,oy=dx/L*28;
 if(pos[e.to]){var cx=mx+ox,cy=my+oy,vx=x2-cx,vy=y2-cy,vl=Math.hypot(vx,vy)||1,hw=e.to===HUB?95:60,hh=e.to===HUB?26:20,tt=Math.min(Math.abs(vx)>1e-6?hw*vl/Math.abs(vx):1e9,Math.abs(vy)>1e-6?hh*vl/Math.abs(vy):1e9)+5;if(tt<vl){x2=Math.round(x2-vx/vl*tt);y2=Math.round(y2-vy/vl*tt);}}  // 矢じりは机の手前で止める（机の下に隠れないように・S23）
 var age=Date.now()-Date.parse(e.ts), isNew=!!(e.ok&&!e.removed&&age<NEW_MS), d='M'+x1+','+y1+' Q'+(mx+ox)+','+(my+oy)+' '+x2+','+y2;
 if(isNew){fresh.push([i,NEW_MS-age]);flows+='<path id="f'+i+'" class="flow" d="'+d+'"/>';}  // 同じ曲線に古い実線が重なっていても白い破線なら見える
 var col=e.to===HUB?'var(--gold)':e.frm===HUB?'var(--cyan)':'var(--mag)'; if(!e.ok)col='var(--red)';
 var rec=(Date.parse(e.ts)-ts0)/((ts1-ts0)||1), op=(0.25+0.75*rec).toFixed(2);
 svg+='<path id="p'+i+'" class="edge'+(e.ok?'':' ng')+(e.removed?' rm':'')+(isNew?' new':'')+'" data-i="'+i+'" d="'+d+'" stroke="'+col+'" opacity="'+op+'" marker-end="url(#ah)"><title>'+esc(e.t+' '+e.frm+' → '+e.to+'\n'+e.summary+(e.preview&&e.preview!==e.summary?'\n'+e.preview:''))+'</title></path>';
 svg+='<g id="env'+i+'" opacity="0"><rect x="-8" y="-6" width="16" height="12" fill="#fff" stroke="#333"/><polyline points="-8,-6 0,2 8,-6" fill="none" stroke="#333"/><animateMotion id="am'+i+'" dur="0.9s" begin="indefinite" fill="remove"><mpath href="#p'+i+'"/></animateMotion></g>';});
svg+=flows;  // 新着の白い破線は全矢印の上・机の下
// 💤 離席中（S23）: プロセスは無いが今日動いた席の主は休憩室のソファへ（新しい順に最大3人・溢れた分は席に半透明）。席には点線枠と 💤
var AWAY=[D.hub].concat(D.tracks).filter(function(t){return !t.live&&t.away&&t.away.length&&pos[t.name];}).sort(function(a,b){return b.away[0].ts-a.away[0].ts;});
AWAY.forEach(function(t,i){awayAt[t.name]=(SOFA&&i<SOFA.length)?i:-1;});
D.tracks.forEach(function(t){var p=pos[t.name];if(p)svg+=desk(p[0],p[1],t,false);});
if(hubVisible)svg+=desk(CX,CY,D.hub,true);
if(SOFA&&AWAY.length){var onS=AWAY.filter(function(t){return awayAt[t.name]>=0;});
 onS.forEach(function(t){var p=SOFA[awayAt[t.name]],a=t.away[0];svg+='<g><title>'+esc('💤 '+t.name+(a.s?' S'+a.s:'')+' 離席中（最後の動き '+a.t+'）\n'+(a.name?a.name+' ':'')+(a.model||'')+'\n席＝'+t.name+'（点線枠）')+'</title>'+sprite(p[0],p[1],MODEL_COL(a.model),a.model,'away',t.name)+'<text class="zz" x="'+(p[0]+28)+'" y="'+(p[1]+4)+'" font-size="12">💤</text></g>';});
 if(onS.length)svg+='<text x="'+SOFA[0][0]+'" y="'+(SOFA[0][1]+66)+'" font-size="10" fill="'+PAL.propTxt+'" fill-opacity=".85">'+esc(fit('💤 '+onS.map(function(t){return t.name+(t.away[0].s?' S'+t.away[0].s:'')+' '+t.away[0].t;}).join(' ／ '),380,10))+'</text>';}
// 人間＝最後に話しかけた席の横に1人（席には座らない・歩き回る）。前回位置を覚えておき、席が変われば歩いて移動する。
var HS=["....HHHH....","...HHHHHH...","...HSSSSH...","...SSSSSS...","...SESSES...","...SSSSSS...","....SSSS....","..GGGGGGGG..",".GGGGGGGGGG.",".G.GGGGGG.G.",".S.GGGGGG.S.","...GGGGGG...","...KKKKKK...","...KK..KK...","...KK..KK...","...BB..BB..."];
var HP={H:'#111',S:'#f2c9a0',E:'#222',G:'#2e9e6b',K:'#444',B:'#222'};
function hsprite(x,y){if(SP.human)return img(SP.human,x,y,null);var s='',sc=3;HS.forEach(function(row,r){row.split('').forEach(function(c,i){if(c==='.')return;s+='<rect x="'+(x+i*sc)+'" y="'+(y+r*sc)+'" width="'+sc+'" height="'+sc+'" fill="'+HP[c]+'"/>';});});return s;}
function hpos(track){var p=pos[track];if(p){var w=track===HUB?190:120;return [p[0]+w/2+6,p[1]-50];}var d=doorOf(track);return d?[d[0]-190,d[1]-50]:null;}  // 隠した部屋ならドアの前
// ☕ 休憩（S22）: 最後の発話から break_after_min 分で人間は休憩室へ（話しかけると席へ戻る・戻る席の点線枠は残す）。しきい値をまたぐ時刻に自前で再読込
var BREAK_MIN=(D.lounge&&D.lounge.break_after_min)||20, mins=D.human?Math.max(0,Math.round((Date.now()-D.human.ts*1000)/60000)):0, onBreak=!!(D.human&&LOUNGE&&mins>=BREAK_MIN);
var hp=D.human?(onBreak?LOUNGE.spot:hpos(D.human.track)):null;
if(hp){var prev=null;try{prev=JSON.parse(localStorage.getItem('csv-human-at')||'null');}catch(e){}
 var from=(prev&&(prev.track!==D.human.track||!!prev.brk!==onBreak))?(prev.brk&&LOUNGE?LOUNGE.spot:hpos(prev.track)):null;
 var ago=mins<1?'いま':mins<60?mins+'分前':Math.floor(mins/60)+'時間'+(mins%60)+'分前';
 var tip=D.human.name+(onBreak?'：☕ 休憩中（最後に話しかけてから '+ago+'）\n戻る席＝':'：最後に話しかけた席＝')+D.human.track+(D.human.s?' S'+D.human.s:'')+'（'+D.human.t+'・'+ago+'）\n「'+D.human.text+'」';
 var dp=pos[D.human.track],dw=D.human.track===HUB?190:120,dh=D.human.track===HUB?52:40;
 if(dp)svg+='<rect x="'+(dp[0]-dw/2-6)+'" y="'+(dp[1]-70)+'" width="'+(dw+12)+'" height="'+(dh+98)+'" rx="8" fill="#2e9e6b" fill-opacity="0.10" stroke="#2e9e6b" stroke-dasharray="6 4" stroke-width="2"/>';
 var hname=onBreak?D.human.name+' 休憩中':D.human.name+(dp?'':'→'+(trackRoom[D.human.track]||'別の部屋'));  // 隠した部屋にいる時は部屋名を添える
 svg+='<g class="human"><title>'+esc(tip)+'</title>'+hsprite(hp[0],hp[1])+
  '<rect x="'+(hp[0]-6)+'" y="'+(hp[1]-22)+'" width="'+(hname.length*11+ago.length*7+30)+'" height="17" rx="3" fill="#fff" stroke="#333"/><text x="'+(hp[0]-2)+'" y="'+(hp[1]-9)+'" font-size="11" fill="#222">'+(onBreak?'☕ ':'👤 ')+esc(hname)+' '+esc(ago)+'</text>'+
  (from?'<animateTransform attributeName="transform" type="translate" from="'+(from[0]-hp[0])+' '+(from[1]-hp[1])+'" to="0 0" dur="1.4s" fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1"/>':'')+'</g>';
 try{localStorage.setItem('csv-human-at',JSON.stringify({track:D.human.track,brk:onBreak}));}catch(e){}
 if(!onBreak&&LOUNGE){var left=BREAK_MIN*60000-(Date.now()-D.human.ts*1000);if(left>0)setTimeout(softReload,Math.min(left+800,2e9));}}
// 💬 吹き出し（S24・個人PC G を移植）: 席の最新の出来事 1 つ（👤 人間の指示／📨→ 送った／📨← 届いた／❌→ 不達）を頭上に。15 分まで濃く・2 時間で薄く。
// ✕＝確認した（SEEN={席名: 閉じた出来事の ts}＝それより新しい出来事でまた出る・このブラウザだけ）。全部の机・人間のあとに描く＝隣の机に隠れない
var BBM=['fresh','all','off'],BBL={fresh:'💬 2時間以内',all:'💬 全部',off:'💬 なし'},bbMode='fresh',HID=0,SEEN={};
try{var bb0=localStorage.getItem('csv-office-bubble');if(BBL[bb0])bbMode=bb0;SEEN=JSON.parse(localStorage.getItem('csv-office-seen')||'{}')||{};}catch(e){SEEN={};}
function seen(n,ts){return SEEN[n]>=ts;}
function markSeen(n,ts){SEEN[n]=ts;var cut=Date.now()-(D.window_hours||48)*3600000;Object.keys(SEEN).forEach(function(k){if(!(SEEN[k]>cut))delete SEEN[k];});try{localStorage.setItem('csv-office-seen',JSON.stringify(SEEN));}catch(e){}}
var SAYC={'👤':'#2e9e6b','📨→':'#5ad1e6','📨←':'#f5c542','❌→':'#ff5a5a'};
function cleanText(s){s=String(s||'').replace(/\s+/g,' ').trim();if(!s)return '';if(s.charAt(0)==='<')return '（ツール操作）';return s;}
function wrapTo(s,w,f,maxLines){var a=Array.from(s),lines=[],cur='';for(var i=0;i<a.length;i++){if(twb(cur+a[i],f)>w){if(lines.length===maxLines-1){lines.push(fit(cur+a.slice(i).join(''),w,f));return lines;}lines.push(cur);cur=a[i];}else cur+=a[i];}if(cur)lines.push(cur);return lines;}
function say(x,yTip,kind,text,ageMin,col,dx,key,big){var f=11,maxW=big?236:168,lines=wrapTo(kind+' '+cleanText(text),maxW,f,big?4:2);if(!lines.length)return '';  // dx＝枠だけ横にずらす（しっぽは x のまま）・big＝本文あり（4 行）
 var w=Math.max.apply(null,lines.map(function(l){return twb(l,f);}))+16,h=lines.length*14+10,x0=Math.round(Math.max(4,Math.min(W-4-w,x-w/2+(dx||0)))),y0=yTip-7-h,op=ageMin<=15?1:ageMin<=120?(1-0.45*(ageMin-15)/105):0.5;
 var tx=Math.max(x0+10,Math.min(x0+w-10,x)),g='<g class="say" opacity="'+op.toFixed(2)+'"><rect x="'+x0+'" y="'+y0+'" width="'+w+'" height="'+h+'" rx="7" fill="#fff" fill-opacity=".96" stroke="'+col+'" stroke-width="1.5"/>';
 g+='<polygon points="'+(tx-5)+','+(y0+h)+' '+(tx+5)+','+(y0+h)+' '+tx+','+yTip+'" fill="#fff" stroke="'+col+'" stroke-width="1.5"/><rect x="'+(tx-5)+'" y="'+(y0+h-2)+'" width="10" height="3" fill="#fff"/>';
 lines.forEach(function(l,i){g+='<text x="'+(x0+8)+'" y="'+(y0+17+14*i)+'" font-size="'+f+'" fill="#222">'+esc(l)+'</text>';});
 if(key)g+='<g class="bx" data-n="'+esc(key.n)+'" data-ts="'+key.ts+'" style="cursor:pointer"><title>確認した＝この吹き出しを閉じる（新しい出来事が来たらまた出る・📨 欄の 💬 を Shift+クリックで全部戻す）</title><circle cx="'+(x0+w)+'" cy="'+y0+'" r="7" fill="#fff" stroke="'+col+'" stroke-width="1.2"/><text x="'+(x0+w)+'" y="'+(y0+3.5)+'" text-anchor="middle" font-size="10" font-weight="700" fill="'+col+'">×</text></g>';
 return g+'</g>';}
function sayFor(t,s,single){var best=null;function put(kind,text,ts,big){if(!text||!(ts>0))return;if(!best||ts>best.ts)best={kind:kind,text:text,ts:ts,big:!!big};}
 var lh=s.live&&s.live.last_human;if(lh&&lh.text)put('👤',lh.text,lh.ts*1000);
 D.edges.forEach(function(e){if(e.removed)return;var ts=Date.parse(e.ts),tx=e.body||e.summary,bg=!!e.body;if(e.frm===t.name&&(single||e.frm_s==null||e.frm_s===s.s))put(e.ok?'📨→':'❌→',e.to+(e.ok?'「':' 不達「')+tx+'」',ts,bg);else if(e.to===t.name&&e.ok&&(single||e.to_s==null||e.to_s===s.s))put('📨←',e.frm+'「'+tx+'」',ts,bg);});
 return best;}
if(bbMode!=='off'){var near=!!(D.human&&!onBreak);
 [D.hub].concat(D.tracks).forEach(function(t){var p=t.name===HUB?(hubVisible?[CX,CY]:null):pos[t.name];if(!p||!t.live||!(t.occupied_by||[]).length)return;
  var big=t.name===HUB,w=big?190:120,bb=null,bi=0,single=t.occupied_by.length===1;t.occupied_by.slice(0,big?3:2).forEach(function(s,i){var b=sayFor(t,s,single);if(b&&(!bb||b.ts>bb.ts)){bb=b;bi=i;}});if(!bb)return;
  var agem=(Date.now()-bb.ts)/60000,mine=bb.kind==='👤'&&near&&D.human.track===t.name&&(D.human.s==null||D.human.s===t.occupied_by[bi].s);  // 人間が隣で同じ言葉を言う時は席側に出さない
  if(mine||!(bbMode==='all'||agem<=120))return;if(seen(t.name,bb.ts)){HID++;return;}
  svg+=say(p[0]-w/2+32+bi*44,p[1]-64,bb.kind,bb.text,agem,SAYC[bb.kind]||'#888',(near&&D.human.track===t.name)?-40:0,{n:t.name,ts:bb.ts},bb.big);});
 if(hp&&D.human.text&&(bbMode==='all'||mins<=120)){if(seen('__human',D.human.ts*1000))HID++;else svg+=say(hp[0]+18,hp[1]-24,'👤',D.human.text,mins,'#2e9e6b',30,{n:'__human',ts:D.human.ts*1000});}}
Object.keys(tints).forEach(function(id){svg+='<filter id="'+id+'" x="0" y="0" width="1" height="1"><feFlood flood-color="'+tints[id]+'" result="c"/><feComposite in="c" in2="SourceAlpha" operator="in" result="m"/><feBlend in="m" in2="SourceGraphic" mode="multiply"/></filter>';});
svg+='</svg>';
document.getElementById('scene').innerHTML=svg;
// 🪟 休憩室の窓: 時刻で昼（6-16時）・夕（17-18時）・夜。毎秒の tick から呼ばれ、時が変わった時だけ描き直す
skyFn=(function(){var last=-1;return function(d){var h=d.getHours();if(h===last||!WIN)return;last=h;var sky=document.getElementById('winsky'),sun=document.getElementById('winsun'),st=document.getElementById('winstars');if(!sky||!sun||!st)return;
 var day=h>=6&&h<17,eve=h>=17&&h<19,f=day?(h-6)/10:eve?(h-17):0.78;
 sky.setAttribute('fill',day?'#7dd3fc':eve?'#fb923c':'#0f172a');sun.setAttribute('fill',day?'#fde047':eve?'#fb7185':'#e2e8f0');
 sun.setAttribute('cx',Math.round(WIN.x+14+(WIN.w-28)*f));sun.setAttribute('cy',Math.round(WIN.y+(day?12+Math.abs(f-0.5)*2*22:eve?34+f*8:16)));st.setAttribute('opacity',(day||eve)?'0':'1');};})();
skyFn(new Date());
var log=document.getElementById('log');
// 並び順: 古い→新しい（最新が下・チャット式）／新しい→古い（最新が上）。localStorage で記憶。日付見出しは順に合わせて組み直す
var ord=document.getElementById('ord'), desc=false; try{desc=localStorage.getItem('csv-log-desc')==='1';}catch(e){}
function renderLog(){log.innerHTML='';var lastDay='';
 if(!D.edges.length){log.innerHTML='<div class="empty">直近 '+D.window_hours+'h にセッション間メッセージはない</div>';return;}
 var idx=D.edges.map(function(_,i){return i;}); if(desc)idx.reverse();
 idx.forEach(function(i){var e=D.edges[i];if(e.day!==lastDay){lastDay=e.day;var d=document.createElement('div');d.className='day';d.textContent=e.day;log.appendChild(d);}
  var r=document.createElement('div');r.className='row';r.dataset.i=i;
  var st=!e.ok?' <span class="ng">❌不達</span>':e.removed?' <span class="rm">🗑破棄</span>':e.read===false?' <span class="un">📨未読</span>':'';
  r.innerHTML='<span class="t">'+e.t.slice(6)+'</span><span class="who">'+esc(e.frm+(e.frm_s?' S'+e.frm_s:''))+'</span> → <span class="who">'+esc(e.to+(e.to_s?' S'+e.to_s:''))+'</span>'+st+'<br><span class="sm">'+esc(e.summary)+'</span>'+(e.body?'<span class="mor">▸ 本文</span><div class="mbd">'+esc(e.body)+'</div>':'');
  r.title=e.body?'クリックで本文を開閉':e.preview; r.onclick=function(ev){if(ev.target.closest('.mbd'))return;hl(i);if(e.body){var o=r.classList.toggle('open');r.querySelector('.mor').textContent=o?'▾ 本文':'▸ 本文';}}; log.appendChild(r);});  // 📨 本文を読む（S24・個人PC H）
 ord.textContent=desc?'⇅ 最新が上':'⇅ 最新が下'; log.scrollTop=desc?0:log.scrollHeight;}
function hl(i){document.querySelectorAll('.hl').forEach(function(x){x.classList.remove('hl');});var p=document.getElementById('p'+i),r=document.querySelector('.row[data-i="'+i+'"]');if(p){p.classList.add('hl');p.parentNode.appendChild(p);}if(r){r.classList.add('hl');r.scrollIntoView({block:'nearest'});}}
document.querySelectorAll('.edge').forEach(function(p){p.onclick=function(){hl(+p.dataset.i);};});
fresh.forEach(function(f){setTimeout(function(){var p=document.getElementById('p'+f[0]);if(p)p.classList.remove('new');var q=document.getElementById('f'+f[0]);if(q)q.remove();},f[1]);});  // 15 分たったら破線を止める（再読込なしでも）
// 👤 再生にふくちゃん（S23）: ▶ で 📨 の流れと今日の人間の席移動（D.human_moves・同じ席が続く分は畳み済み・空きは ☕ 経由）を時刻順に混ぜて再生。
// 再生中は在席の 👤 を隠し、終わったら戻す。項目が多い時は 1 歩を縮める（50 件超で最短 0.5 秒）
var HN=D.human_name||'人間', MV=(D.human_moves||[]).map(function(m){return {kind:'mv',ts:m.ts*1000,m:m};});
var RF=(D.replay_from||0)*1000;  // 再生は今日の分だけ（away_cutoff＝今日 0:00 以降。📨 の一覧は 48h のまま）
var RTL=D.edges.map(function(e,i){return {kind:'msg',ts:Date.parse(e.ts),i:i};}).concat(MV).filter(function(it){return it.ts>=RF;}).sort(function(a,b){return a.ts-b.ts;}), playing=false;
function tw(s){var w=0;for(var i=0;i<s.length;i++)w+=s.charCodeAt(i)>255?11:6.5;return Math.round(w);}
function rhuman(p){var g=document.createElementNS('http://www.w3.org/2000/svg','g');g.id='rhuman';g.innerHTML='<title id="rhtt"></title>'+hsprite(0,0)+'<rect id="rhb" x="-6" y="-22" width="80" height="17" rx="3" fill="#fff" stroke="#333"/><text id="rht" x="-2" y="-9" font-size="11" fill="#222"></text>';
 g.style.transform='translate('+p[0]+'px,'+p[1]+'px)';document.querySelector('#scene svg').appendChild(g);g.getBoundingClientRect();g.style.transition='transform .7s cubic-bezier(.4,0,.2,1)';return g;}
function moveH(m){var g=document.getElementById('rhuman');if(!g)return;var p=m.lounge?(LOUNGE?LOUNGE.spot:null):hpos(m.track);if(!p)return;
 g.style.transform='translate('+p[0]+'px,'+p[1]+'px)';var lab=m.lounge?'☕ '+HN+' 休憩 '+m.t:'👤 '+HN+' → '+m.track+(m.s?' S'+m.s:'')+' '+m.t;
 document.getElementById('rht').textContent=lab;document.getElementById('rhb').setAttribute('width',tw(lab)+10);
 document.getElementById('rhtt').textContent=m.lounge?HN+'：'+BREAK_MIN+'分以上話しかけていない→休憩室（'+m.t+'）':HN+' → '+m.track+(m.s?' S'+m.s:'')+' '+m.t+(m.n>1?'（続けて '+m.n+' 回）':'')+'「'+(m.text||'')+'」';}
// 🕒 再生中のダイジェスト（S23 ⑬）: ヘッダの凡例の場所に「時刻＋何があったか＋k/N＋進捗」。終わったら 2.5 秒見せて凡例に戻す
var HDR=document.querySelector('header'),RB={clk:document.getElementById('rclk'),cap:document.getElementById('rcap'),cnt:document.getElementById('rcnt'),prog:document.getElementById('rprog')};
function digest(it,k){var t,cap;if(it.kind==='mv'){var m=it.m;t=m.t;cap=m.lounge?'☕ '+HN+' → 休憩室（'+BREAK_MIN+'分以上話しかけていない）':'👤 '+HN+' → '+m.track+(m.s?' S'+m.s:'')+(m.n>1?'（続けて '+m.n+' 回）':'')+(m.text?' 「'+m.text+'」':'');}
 else{var e=D.edges[it.i];t=(e.t||'').slice(-5);cap='📨 '+e.frm+(e.frm_s?' S'+e.frm_s:'')+' → '+e.to+(e.to_s?' S'+e.to_s:'')+(e.ok?'':' ❌ 不達')+(e.summary?' ・ '+e.summary:'');}
 RB.clk.textContent=t||'';RB.cap.textContent=cap;RB.cap.title=cap;RB.cnt.textContent=(k+1)+' / '+RTL.length;RB.prog.style.width=((k+1)*100/RTL.length)+'%';}
document.getElementById('play').onclick=function(){if(playing||!RTL.length)return;playing=true;window.csvPlaying=true;var STEP=RTL.length>50?Math.max(500,Math.round(47500/RTL.length)):950;
 HDR.classList.add('playing');RB.clk.textContent='▶';RB.cap.textContent='今日の流れを再生（'+RTL.length+' 件・1 歩 '+(STEP/1000)+' 秒）';RB.cnt.textContent='';RB.prog.style.width='0%';
 document.querySelectorAll('#scene .human').forEach(function(x){x.style.visibility='hidden';});
 if(MV.length){var p0=(LOUNGE&&LOUNGE.spot)||hpos(MV[0].m.track);if(p0)rhuman(p0);}
 RTL.forEach(function(it,k){setTimeout(function(){digest(it,k);if(it.kind==='mv'){moveH(it.m);return;}hl(it.i);var g=document.getElementById('env'+it.i),a=document.getElementById('am'+it.i);if(!g)return;g.setAttribute('opacity','1');a.beginElement();setTimeout(function(){g.setAttribute('opacity','0');},900);},k*STEP);});
 setTimeout(function(){var g=document.getElementById('rhuman');if(g)g.remove();document.querySelectorAll('#scene .human').forEach(function(x){x.style.visibility='';});playing=false;window.csvPlaying=false;RB.clk.textContent='⏹';RB.cap.textContent='再生おわり（'+RTL.length+' 件）';setTimeout(function(){HDR.classList.remove('playing');},2500);if(window.csvReloadAfter)location.reload();},RTL.length*STEP+900);};
ord.onclick=function(){desc=!desc;try{localStorage.setItem('csv-log-desc',desc?'1':'0');}catch(e){}renderLog();};
renderLog();
(function(){var b=document.getElementById('bb');if(!b)return;function lab(){b.textContent=BBL[bbMode]+(HID?'・✕'+HID:'');}lab();  // 💬 クリック＝2時間以内→全部→なし・Shift+クリック＝✕ で閉じた吹き出しを全部戻す
 b.onclick=function(ev){if(ev.shiftKey){try{localStorage.removeItem('csv-office-seen');}catch(e){}location.reload();return;}try{localStorage.setItem('csv-office-bubble',BBM[(BBM.indexOf(bbMode)+1)%BBM.length]);}catch(e){}location.reload();};
 document.querySelectorAll('.bx').forEach(function(x){x.addEventListener('click',function(ev){ev.stopPropagation();markSeen(x.dataset.n,+x.dataset.ts);var sy=x.closest('.say');if(sy)sy.remove();HID++;lab();});});})();
})();
"""

_CODE_VER = hashlib.sha1((CSS + PAGE_JS).encode()).hexdigest()[:8]


def render_office(D: dict, cfg: dict, stamp: str, poll_ms: int) -> str:
    hub = (cfg.get("office") or {}).get("hub", "業務ハブ")
    c = D["counts"]
    now = datetime.now().strftime("%m/%d %H:%M:%S")
    tiles = (f'<div class="tiles"><span class="tile"><b>{c["live"]}</b>人 在席</span>'
             f'<span class="tile"><b>{c.get("away", 0)}</b>席 離席中</span>'
             f'<span class="tile"><b>{c.get("leave", 0)}</b>席 退社</span>'
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
              '<span>｜ 💤 離席中＝プロセスは無いが今日動いた席（主は休憩室のソファ・席は点線枠）</span>'
              '<span>｜ 🏠 退社＝最後の発話が文頭「退社」の席（復帰日まで主は不在・「退社 次 10/20」で復帰日指定・既定は次の平日。人間の最後の発話が退社なら 👤 も帰宅）</span>'
              '<span>｜ ▶ 再生＝今日の 📨 の流れ＋👤 の席移動（空きは休憩室経由・再生中は再読込を保留）</span>'
              '<span>｜ 📱 Remote Control オン（外出先から指示できる席）</span>'
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
<div class="sub">いま <b id="clock"></b> ／ 生成 {now} ／ 席=トラック・人=稼働セッション・矢印=SendMessage（新しいほど濃い・15分以内は動く）・席にマウスで詳細</div></div>
{tiles}
<button class="tgl" id="play" type="button" title="今日の 📨 の流れと 👤 の席移動を時刻順に再生（再生中は自動更新を保留）">▶ 流れを再生</button>
<div class="navs">{nav_html("office")}<button class="tgl" id="tgl" type="button"></button></div>
<div style="flex-basis:100%">{legend}</div>
<div id="rbar" title="▶ 再生中のダイジェスト＝その時刻に何があったか"><b id="rclk"></b><span id="rcap"></span><span id="rcnt"></span><div id="rtrk"><div id="rprog"></div></div></div>
</header>
<main><div id="scene"></div><aside><h2><span title="行クリックで矢印を強調・本文を開閉">📨 やり取り</span><button class="tgl" id="bb" type="button" title="席の吹き出し（クリックで 2時間以内→全部→なし・Shift+クリックで ✕ で閉じたものを全部戻す）"></button><button class="tgl" id="ord" type="button"></button></h2><div id="log"></div></aside></main>
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
