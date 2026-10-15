"""セッション間メッセージ（SendMessage）の収集と突合（汎用コア・読み取り専用）。

新しい記録場所は作らない。各セッションの jsonl と `~/.claude/sessions/*.json`（稼働台帳）だけで
「誰が・いつ・誰に・何を送り・相手が読んだか」を復元する（S21・2026-10-15）。

jsonl に残る痕跡（実測 2026-10-15）:
  送信  assistant レコードの tool_use name=SendMessage（input: to / summary / message）
        → 直後の user レコードの tool_result に {"success":true/false, "message": "“要約” → 宛先 …"}
  受信  type=queue-operation operation=enqueue content=<cross-session-message from="uds:…pipe…"
        from-name="…" from-mode="…">本文</cross-session-message>   （受け手の受信箱に入った）
  既読  type=user isMeta=true origin={kind:"peer", from, name, body}  （idle 時: 受け手の Claude が読んだ）
        type=attachment attachment.type=queued_command prompt=<cross-session-message…>（busy 時: 今のターンに差し込み）
  破棄  type=queue-operation operation=remove（content に同じタグ）   （承認されず消えた等）
  送り手の pipe（cc-msg-<hash>）＝稼働台帳 sessions/<pid>.json の messagingSocketPath。
  プロセスが生きている間しか引けないので、見えた対応は cache に学習して残す。

突合: 受信（本文一致・時刻差 ≤ MATCH_SEC）→ 送信。足りない側は pipe / 宛先名 → sid で補う。
席（トラック）の identity は from-name ではなく sid × ProjectMapper（採番合言葉）で決める。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .parser import _clean_user_text, _iter_text_items

CACHE_VERSION = 1
MATCH_SEC = 60          # 送信→受信箱投入の時刻差の許容（実測は 1〜3 秒）
PEEK_LINES = 400        # 採番合言葉は先頭にある前提（live_board._peek と同じ）
_HEX17 = re.compile(r"^[0-9a-f]{17}$")   # in-process サブエージェントの agentId（横断ではない）
_RECV_RE = re.compile(
    r'<cross-session-message\s+from="(?P<frm>[^"]*)"(?:\s+from-name="(?P<name>[^"]*)")?'
    r'(?:\s+from-mode="(?P<mode>[^"]*)")?\s*>(?P<body>.*?)</cross-session-message>', re.S)
_PIPE_RE = re.compile(r"cc-msg-([0-9a-f]{16,})")


def _norm_body(s: str) -> str:
    return (s or "").strip()


def _pipe_hash(s: str):
    m = _PIPE_RE.search(s or "")
    return m.group(1) if m else None


def _epoch(ts_iso):
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- 1ファイルの差分読み

def _scan_file(path: Path, state: dict) -> dict:
    """jsonl を前回オフセットから読み足し、state（sends/recvs/pending/offset）を更新して返す。

    jsonl は追記専用なので、末尾の未完行を除いた位置をオフセットに保てば差分読みで足りる。
    """
    size = path.stat().st_size
    off = state.get("offset", 0)
    if size < off:  # 作り直された（想定外）→ 全読み
        state = {"offset": 0, "sends": [], "recvs": [], "pending": {}}
        off = 0
    if size == off:
        return state
    with open(path, "rb") as f:
        f.seek(off)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return state
    text = chunk[:end + 1].decode("utf-8", errors="replace")
    state["offset"] = off + end + 1
    sends, recvs, pending = state["sends"], state["recvs"], state["pending"]
    sid = path.stem
    for line in text.splitlines():
        if not line:
            continue
        hot = ('"SendMessage"' in line or "cross-session-message" in line
               or '"kind":"peer"' in line or '"kind": "peer"' in line
               or (pending and "tool_result" in line and any(k in line for k in pending)))
        if not hot:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = rec.get("type")
        ts = rec.get("timestamp") or ""
        if t == "assistant":
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for it in content:
                if not (isinstance(it, dict) and it.get("type") == "tool_use"
                        and it.get("name") == "SendMessage"):
                    continue
                inp = it.get("input") or {}
                to = (inp.get("to") or "").strip()
                if not to or to == "main" or _HEX17.match(to):
                    continue  # in-process のサブエージェント宛＝横断ではない
                ev = {"sid": sid, "ts": ts, "to": to, "summary": inp.get("summary") or "",
                      "body": _norm_body(inp.get("message")), "ok": None, "result": ""}
                sends.append(ev)
                pending[it.get("id") or f"noid-{len(sends)}"] = len(sends) - 1
        elif t == "user":
            msg = rec.get("message") or {}
            origin = rec.get("origin") or {}
            if origin.get("kind") == "peer":  # 受け手の Claude が読んだ瞬間（idle 時の配達）
                if "cc-msg" in (origin.get("from") or ""):  # サブエージェントの hand-back は除く
                    recvs.append({"sid": sid, "ts": ts, "from": origin.get("from") or "",
                                  "from_name": origin.get("name") or "",
                                  "body": _norm_body(origin.get("body")),
                                  "stage": "read"})
                continue
            content = msg.get("content")
            if isinstance(content, list) and pending:
                for it in content:
                    if not (isinstance(it, dict) and it.get("type") == "tool_result"):
                        continue
                    idx = pending.pop(it.get("tool_use_id"), None)
                    if idx is None:
                        continue
                    res = it.get("content")
                    if isinstance(res, list):
                        res = " ".join(x.get("text", "") for x in res if isinstance(x, dict))
                    res = res or ""
                    ev = sends[idx]
                    ev["result"] = res[:300]
                    ev["ok"] = ('"success":true' in res or '"success": true' in res)
                    # 宛先の実体が pipe（返信）なら pipe を記録。名前宛なら結果文の「→ 名前 (another
                    # Claude session…)」で横断確定。それ以外（Agent "…" resumed 等）は in-process。
                    if "another Claude session" not in res and "uds:" not in res \
                            and "No agent named" not in res and "No running session" not in res \
                            and not ev["to"].startswith("uds:"):
                        ev["ok"] = None
                        ev["inproc"] = True
        elif t == "attachment":
            # busy 中の配達: attachment(queued_command) として今のターンに差し込まれる。
            # この後に queue-operation remove が来るが、それは「読まれた」であって破棄ではない。
            att = rec.get("attachment") or {}
            if att.get("type") != "queued_command":
                continue
            m = _RECV_RE.search(att.get("prompt") or "")
            if m and "cc-msg" in m.group("frm"):
                recvs.append({"sid": sid, "ts": ts, "from": m.group("frm"),
                              "from_name": m.group("name") or "",
                              "body": _norm_body(m.group("body")), "stage": "read"})
        elif t == "queue-operation":
            m = _RECV_RE.search(rec.get("content") or "")
            if not m:
                continue
            stage = "inbox" if rec.get("operation") == "enqueue" else (
                "removed" if rec.get("operation") == "remove" else None)
            if stage:
                recvs.append({"sid": sid, "ts": ts, "from": m.group("frm"),
                              "from_name": m.group("name") or "",
                              "body": _norm_body(m.group("body")), "stage": stage})
    state["sends"] = [s for s in sends if not s.get("inproc")]
    # pending のインデックスは sends のフィルタで狂うので、未解決分は捨てずに本文で引き直せる形に
    # しておく（結果が次の差分で来るのは稀＝最後の tool_use が末尾にある時のみ）
    state["pending"] = {k: v for k, v in pending.items() if v < len(state["sends"])}
    return state


# ---------------------------------------------------------------- 全体の収集

def _load_cache(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("version") == CACHE_VERSION:
            return c
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": CACHE_VERSION, "files": {}, "pipe_map": {}, "names": {}}


def _save_cache(path: Path, cache: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    tmp.replace(path)


def load_registry(claude_dir: Path) -> dict:
    """稼働台帳 sessions/<pid>.json を sid → レコードで返す（claude agents --json と同内容）。"""
    out = {}
    sdir = claude_dir / "sessions"
    if not sdir.is_dir():
        return out
    for p in sdir.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        sid = rec.get("sessionId")
        if sid and (sid not in out or rec.get("startedAt", 0) > out[sid].get("startedAt", 0)):
            out[sid] = rec
    return out


def _learn_registry(cache: dict, registry: dict):
    """pipe→sid と 名前→sid（開始時刻付き）を cache に学習。プロセスが死んだ後も引けるようにする。"""
    for sid, rec in registry.items():
        h = _pipe_hash(rec.get("messagingSocketPath") or "")
        if h:
            cache["pipe_map"][h] = sid
        name = rec.get("name")
        if name:
            lst = cache["names"].setdefault(name, [])
            if not any(x["sid"] == sid for x in lst):
                lst.append({"sid": sid, "started": (rec.get("startedAt") or 0) / 1000.0})


def _sid_by_pipe(cache: dict, frm: str):
    h = _pipe_hash(frm)
    return cache["pipe_map"].get(h) if h else None


def _sid_by_name(cache: dict, name: str, ts_epoch: float):
    """宛先名 → その時刻に生きていた可能性が最も高い sid（開始が ts 以前で最新のもの）。"""
    cands = [x for x in cache["names"].get(name, []) if x["started"] <= ts_epoch + MATCH_SEC]
    if not cands:
        return None
    return max(cands, key=lambda x: x["started"])["sid"]


def collect(claude_dir: Path, cache_path: Path, window_hours: float = 48,
            registry: dict | None = None) -> dict:
    """直近 window_hours に更新された全セッションからメッセージ痕跡を集めて突合し、edge 一覧を返す。

    戻り値: {"edges": [...], "sids": {関係した sid}, "registry": {...}, "span": [first_ts, last_ts]}
    edge = {ts, frm, frm_label(名前のみ), to, to_label, summary, preview, body, ok,
            read: True/False/None, removed, matched}
    """
    cache = _load_cache(cache_path)
    registry = registry if registry is not None else load_registry(claude_dir)
    _learn_registry(cache, registry)

    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - window_hours * 3600
    files = cache["files"]
    seen = set()
    projects_dir = claude_dir / "projects"
    for ws_dir in projects_dir.iterdir() if projects_dir.is_dir() else []:
        if not ws_dir.is_dir():
            continue
        for jsonl in ws_dir.glob("*.jsonl"):
            try:
                st = jsonl.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            key = str(jsonl)
            seen.add(key)
            state = files.get(key) or {"offset": 0, "sends": [], "recvs": [], "pending": {}}
            try:
                files[key] = _scan_file(jsonl, state)
            except OSError:
                continue
    # 窓から外れたファイルは落とす（cache を肥大させない。戻ってきたら全読みし直すだけ）
    for key in [k for k in files if k not in seen]:
        files.pop(key)
    _save_cache(cache_path, cache)

    sends, recvs = [], []
    for st in files.values():
        sends.extend(st["sends"])
        recvs.extend(st["recvs"])
    sends = [s for s in sends if _epoch(s["ts"]) >= cutoff]
    recvs = [r for r in recvs if _epoch(r["ts"]) >= cutoff]
    sends.sort(key=lambda s: s["ts"])
    recvs.sort(key=lambda r: r["ts"])

    # 受信側を「受信箱投入 → 既読 → 破棄」の段階でまとめる（本文＋受け手 sid 単位・順番で対応）
    inbox = {}
    for r in recvs:
        k = (r["sid"], r["body"])
        if r["stage"] == "inbox":
            inbox.setdefault(k, []).append({"ts": r["ts"], "from": r["from"],
                                            "from_name": r["from_name"], "read": None,
                                            "removed": False})
        else:
            lst = inbox.setdefault(k, [])
            tgt = next((x for x in lst if (x["read"] is None and not x["removed"])), None)
            if tgt is None:
                if r["stage"] == "removed":
                    continue  # 既読後のキュー掃除（queued_command 配達の後片付け）＝破棄ではない
                # 受信箱投入の記録が無い（古い形式等）→ 既読から起こす
                tgt = {"ts": r["ts"], "from": r["from"], "from_name": r["from_name"],
                       "read": None, "removed": False}
                lst.append(tgt)
            if r["stage"] == "read":
                tgt["read"] = r["ts"]
            else:
                tgt["removed"] = True
    recv_items = []
    for (sid, body), lst in inbox.items():
        for x in lst:
            recv_items.append({"sid": sid, "body": body, **x, "used": False})
    recv_items.sort(key=lambda x: x["ts"])

    edges = []
    for s in sends:
        if s["ok"] is False:
            edges.append(_edge(s, None, cache))
            continue
        te = _epoch(s["ts"])
        hit = None
        for r in recv_items:
            if r["used"] or r["body"] != s["body"]:
                continue
            if abs(_epoch(r["ts"]) - te) <= MATCH_SEC:
                hit = r
                break
        if hit:
            hit["used"] = True
        edges.append(_edge(s, hit, cache))
    # 送信側の痕跡が無い受信（送り手が別マシン／窓の外／別 claude_dir）
    for r in recv_items:
        if r["used"]:
            continue
        frm = _sid_by_pipe(cache, r["from"])
        edges.append({"ts": r["ts"], "frm": frm, "frm_label": r["from_name"] or "?",
                      "to": r["sid"], "to_label": "", "summary": "",
                      "preview": _first_line(r["body"]), "body": r["body"], "ok": True,
                      "read": bool(r["read"]) if not r["removed"] else None,
                      "removed": r["removed"], "matched": False})
    edges.sort(key=lambda e: e["ts"])
    sids = {e["frm"] for e in edges if e["frm"]} | {e["to"] for e in edges if e["to"]}
    span = [edges[0]["ts"], edges[-1]["ts"]] if edges else None
    return {"edges": edges, "sids": sids, "registry": registry, "span": span}


def _first_line(body: str, limit: int = 120) -> str:
    for ln in (body or "").splitlines():
        ln = ln.strip()
        if ln:
            return ln[:limit]
    return ""


def _edge(s: dict, hit: dict | None, cache: dict) -> dict:
    to_sid = hit["sid"] if hit else None
    if to_sid is None:
        if s["to"].startswith("uds:"):
            to_sid = _sid_by_pipe(cache, s["to"])
        else:
            to_sid = _sid_by_name(cache, s["to"].split(" [")[0], _epoch(s["ts"]))
    return {"ts": s["ts"], "frm": s["sid"], "frm_label": "", "to": to_sid,
            "to_label": "" if s["to"].startswith("uds:") else s["to"],
            "summary": s["summary"], "preview": _first_line(s["body"]), "body": s["body"],
            "ok": s["ok"] if s["ok"] is not None else True,
            "read": (bool(hit["read"]) if hit and not hit["removed"] else None) if hit else None,
            "removed": bool(hit and hit["removed"]), "matched": hit is not None}


# ---------------------------------------------------------------- 席の identity（sid → トラック）

_PATH_CACHE: dict = {}


def find_transcript(claude_dir: Path, sid: str):
    p = _PATH_CACHE.get(sid)
    if p and Path(p).exists():
        return Path(p)
    for cand in (claude_dir / "projects").glob(f"*/{sid}.jsonl"):
        _PATH_CACHE[sid] = str(cand)
        return cand
    return None


def peek_summary(transcript: Path, workspace: str) -> dict:
    """冒頭だけ読んで採番マッチ用の擬似サマリーを作る（live_board._peek と同じ・全パースしない）。"""
    summary = {"ai_title": None, "first_user_text": None, "last_prompt": None,
               "user_texts_matchpool": [], "workspace": workspace}
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= PEEK_LINES:
                    break
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get("type")
                if t == "ai-title":
                    summary["ai_title"] = rec.get("aiTitle") or summary["ai_title"]
                elif t == "user" and not rec.get("isMeta") and not rec.get("isSidechain"):
                    for raw in _iter_text_items((rec.get("message") or {}).get("content")):
                        text = _clean_user_text(raw)
                        if text:
                            if summary["first_user_text"] is None:
                                summary["first_user_text"] = text[:200]
                            if len(summary["user_texts_matchpool"]) < 10:
                                summary["user_texts_matchpool"].append(text[:120])
                            break
                    if len(summary["user_texts_matchpool"]) >= 10:
                        break
    except OSError:
        pass
    return summary


def label_sessions(sids, claude_dir: Path, mapper, ws_labels: dict | None = None,
                   known: dict | None = None) -> dict:
    """sid → {"project", "s", "ws", "title"}。known（live_board の rows 等）があれば優先して使う。"""
    ws_labels = ws_labels or {}
    out = {}
    for sid in sids:
        if known and sid in known:
            out[sid] = known[sid]
            continue
        tr = find_transcript(claude_dir, sid)
        if tr is None:
            out[sid] = {"project": None, "s": None, "ws": "", "title": ""}
            continue
        ws = tr.parent.name
        summ = peek_summary(tr, ws)
        mapper.map_session(summ)
        out[sid] = {"project": summ.get("project"), "s": summ.get("session_no"),
                    "ws": ws_labels.get(ws, ws), "title": (summ.get("first_user_text") or "")[:80]}
    return out


def hub_names(cfg: dict) -> set:
    """業務ハブとみなす案件名の集合（表示名＋alias の正規名・別名すべて）。"""
    hub = (cfg.get("office") or {}).get("hub", "業務ハブ")
    names = {hub}
    for canonical, aliases in (cfg.get("project_aliases") or {}).items():
        if hub == canonical or hub in aliases:
            names.add(canonical)
            names.update(aliases)
    return names


def _fmt_local(ts_iso: str) -> str:
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone().strftime("%m/%d %H:%M")
    except (AttributeError, ValueError):
        return "?"


def display_name(label: dict | None, fallback: str, cfg: dict) -> str:
    """edge の端点の表示名: トラック名＋S番号。ハブは表示名に正規化。未分類は fallback（宛先名）。"""
    if not label or not label.get("project"):
        return fallback or "？"
    pj = label["project"]
    if pj in hub_names(cfg):
        pj = (cfg.get("office") or {}).get("hub", "業務ハブ")
    return f"{pj} S{label['s']}" if label.get("s") else pj


def recent_html(edges, labels, cfg, limit=10, esc=None) -> str:
    """ライブボード用「📨 最近のやり取り」テーブル（新しい順・limit 件）。CSS はボード側の table 流用。"""
    import html as html_mod
    esc = esc or html_mod.escape
    if not edges:
        return ('<div class="empty">なし — 直近のセッション間メッセージ（SendMessage）はない</div>')
    out = []
    for e in list(reversed(edges))[:limit]:
        frm = display_name(labels.get(e["frm"]), e["frm_label"], cfg)
        to = display_name(labels.get(e["to"]), e["to_label"], cfg)
        if e["ok"] is False:
            badge, rail = '<span class="badge">❌ 不達</span>', "#d03b3b"
        elif e["removed"]:
            badge, rail = '<span class="badge">🗑 破棄</span>', "#898781"
        elif e["read"]:
            badge, rail = '<span class="badge">✅ 既読</span>', "#0ca30c"
        elif e["read"] is False:
            badge, rail = '<span class="badge">📨 未読</span>', "#e0a800"
        else:
            badge, rail = '<span class="badge">📤 送信</span>', "#3987e5"
        text = e["summary"] or e["preview"]
        out.append(
            f'<tr style="--rail:{rail}"><td>{badge}</td>'
            f'<td class="pj">{esc(frm)}</td><td class="sn">→</td><td class="pj">{esc(to)}</td>'
            f'<td class="up">{esc(_fmt_local(e["ts"]))}</td>'
            f'<td class="title" title="{esc(e["preview"])}">{esc(text[:90])}</td></tr>')
    return f'<table>{"".join(out)}</table>'


if __name__ == "__main__":  # 単発確認: python -m claude_sv.messages [--hours N]
    import sys
    from .mapping import ProjectMapper, load_config
    sys.stdout.reconfigure(encoding="utf-8")
    repo = Path(__file__).resolve().parent.parent
    cfg = load_config(repo)
    hours = 48
    if "--hours" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--hours") + 1])
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    res = collect(claude_dir, repo / "cache" / "messages_cache.json", hours)
    labels = label_sessions(res["sids"], claude_dir, ProjectMapper(cfg), cfg.get("workspace_labels"))
    n_matched = sum(1 for e in res["edges"] if e["matched"])
    print(f"edges {len(res['edges'])}（突合 {n_matched}）／関係セッション {len(res['sids'])}／窓 {hours}h")
    for e in res["edges"]:
        frm = display_name(labels.get(e["frm"]), e["frm_label"], cfg)
        to = display_name(labels.get(e["to"]), e["to_label"], cfg)
        st = ("❌不達" if e["ok"] is False else "🗑破棄" if e["removed"] else
              "✅既読" if e["read"] else "📨未読" if e["read"] is False else "📤")
        print(f"{_fmt_local(e['ts'])} {st:<4} {frm} → {to} ｜ {(e['summary'] or e['preview'])[:50]}"
              + ("" if e["matched"] else "  (片側のみ)"))
