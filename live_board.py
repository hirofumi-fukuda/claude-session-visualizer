"""並行セッションのライブボード（照準②の最小ビュー）。

データ源:
  1. claude agents --json                 … 稼働中セッション一覧（公式・pid/cwd/sessionId）
  2. ~/.claude/hooks/session_state/*.json … 状態判定の主軸。session_notify.py が UserPromptSubmit=
                                             working / Stop=waiting_input / Notification=waiting_permission
                                             を書く（＝ターンの境界信号）。判定詳細は resolve_state。
  3. transcript(jsonl) の更新時刻           … hook状態が無いセッションのフォールバック＋新ターン検知。
                                             直近 fresh_sec 秒に追記=🟢作業中。
  4. claude_sv.mapping                    … 採番規約→案件名の解決（冒頭発話をピーク読み）

  ※ 状態モデルの変遷: S5 は「Cursorはフックが発火しない」と誤診し jsonl mtime を主軸にしたが、
    S6（2026-07-30）で真因は「powershell -File フック不実行・python は動く」と判明→session_notify
    を python化しフックが発火するように。状態は hook（ターン境界）を主軸に戻し jsonl mtime は
    フォールバックへ。これで長いターンの追記の谷間でも🟢を維持できる（旧＝谷間で🟡へ誤落ち）。

使い方: python live_board.py                    # ターミナルに1回表示
        python live_board.py --watch 10         # 10秒ごとに再表示（Ctrl+Cで終了）
        python live_board.py --html --watch 10  # reports/live_board.html を書き換え続ける
                                                # （ブラウザ側はメタリフレッシュで自動再読込。
                                                #   ワンクリック起動は live_board.bat）
"""
import html as html_mod
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.parser import _clean_user_text, _iter_text_items

REPO_ROOT = Path(__file__).parent
PEEK_LINES = 400  # 冒頭発話ピークの読み込み上限（採番合言葉は先頭にある前提）

STATE_LABELS = {
    "working": "🟢 作業中",
    "waiting_input": "🟡 待機中",
    "waiting_permission": "🔴 許可待ち",
}
STATE_ORDER = {"waiting_permission": 0, "waiting_input": 1, "working": 2}


def resolve_state(now_epoch, mtime, hook_state, hook_ts_epoch, fresh_sec):
    """セッションの状態を決める（S6設計・S7で谷間対策）。

    hook 状態（UserPromptSubmit=working / Stop=waiting_input / Notification=waiting_permission）
    を「ターンの境界信号」として主軸に据える。<sid>.json は常に最新イベントで上書きされるので、
    hook_state==working は「UserPromptSubmit 済み・まだ Stop していない＝ターン進行中」を意味する。
    これは jsonl の追記が途切れる谷間（長いツール実行・長い生成）でも有効なので、鮮度で足切り
    しない（S5→S6以前は hk_age<fresh_sec で足切りしていたため、長いターンの合間に🟡へ誤落ちして
    いた＝ふくちゃん報告のバグ）。jsonl mtime は hook 状態が無いセッション（フック配線前 等）の
    フォールバック。待ち系(Stop/許可待ち)の後に jsonl が新しく動いていれば、境界を跨いだ新ターン
    とみなして working に戻す（UserPromptSubmit を取りこぼした場合の保険）。
    """
    jsonl_fresh = (now_epoch - mtime) < fresh_sec
    if hook_state in ("working", "waiting_input", "waiting_permission"):
        if hook_state == "working":
            return "working"  # ターン進行中。谷間でも維持（次の hook イベントまで有効）
        # 待ち系: 直近の hook より後に jsonl が動き、かつ新鮮なら新ターン進行中とみなす
        if jsonl_fresh and hook_ts_epoch is not None and mtime > hook_ts_epoch:
            return "working"
        return hook_state
    return "working" if jsonl_fresh else "waiting_input"


def _agents_json():
    """claude agents --json で稼働中セッション一覧を取得（sessionId 重複は新しい方を残す）。"""
    try:
        r = subprocess.run("claude agents --json", shell=True, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        recs = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    by_sid = {}
    for rec in recs:
        sid = rec.get("sessionId")
        if not sid:
            continue
        if sid not in by_sid or rec.get("startedAt", 0) > by_sid[sid].get("startedAt", 0):
            by_sid[sid] = rec
    return by_sid


def _load_states(claude_dir: Path):
    state_dir = claude_dir / "hooks" / "session_state"
    states = {}
    if not state_dir.is_dir():
        return states
    for p in state_dir.glob("*.json"):
        try:
            states[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return states


def _enc_ws(cwd: str) -> str:
    """cwd → ~/.claude/projects 配下のワークスペースディレクトリ名（非英数字→'-'）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


# ─── 休眠セッション管理（S7）─────────────────────────────────────────────
# 「休眠」＝タブを閉じてプロセスは死んでいるが、後で --resume して続けたいセッション。
# クローズとの区別はデータ上つかない（どちらもプロセス死）→ 区別は人間の意図のみ。
# よって「明示ピン（config.local の pinned_sessions[]）」を真実の芯に据え（B案）、全手動は
# 続かないので「案件マップ済み・プロセス停止中・直近更新」を再開候補として自動提示する（A案）。
# 前提（S7で実証）: --resume は同一 jsonl に同一 sessionId で追記する（複数日にまたがるファイル
# 多数・内部sidは常に単一でファイル名一致）→ sid はピンのキーとして跨ぎで安定＝迷子にならない。

def _iter_session_files(claude_dir: Path):
    """~/.claude/projects 配下の非agentトップレベルセッション jsonl を (sid, path, ws) で列挙。"""
    proj = claude_dir / "projects"
    if not proj.is_dir():
        return
    for ws_dir in proj.iterdir():
        if not ws_dir.is_dir():
            continue
        for jf in ws_dir.glob("*.jsonl"):
            if jf.name.startswith("agent-"):
                continue
            yield jf.stem, jf, ws_dir.name


def _find_by_sid(claude_dir: Path, sid: str):
    for s, jf, ws in _iter_session_files(claude_dir):
        if s == sid:
            return jf, ws
    return None, None


def _resolve_sid_prefix(claude_dir: Path, prefix: str):
    """sid接頭辞から一意にセッションを特定（CLIでフルUUIDを打たなくて済むように）。"""
    matches = [(s, jf, ws) for s, jf, ws in _iter_session_files(claude_dir) if s.startswith(prefix)]
    if len(matches) == 1:
        return matches[0][0], None
    if not matches:
        return None, f"該当セッションなし: {prefix}"
    return None, (f"曖昧（{len(matches)}件マッチ・接頭辞を伸ばして）: "
                  + ", ".join(m[0][:12] for m in matches[:6]))


def _config_local_path() -> Path:
    return REPO_ROOT / "config.local.json"


def _load_pins(cfg: dict):
    """pinned_sessions を正規化（[{sid, note, pinned_at}]）。素のsid文字列も後方互換で許容。"""
    out = []
    for p in cfg.get("pinned_sessions", []) or []:
        if isinstance(p, str):
            out.append({"sid": p, "note": "", "pinned_at": None})
        elif isinstance(p, dict) and p.get("sid"):
            out.append({"sid": p["sid"], "note": p.get("note", ""),
                        "pinned_at": p.get("pinned_at")})
    return out


def _save_pins(pins):
    """config.local.json の pinned_sessions のみ差し替えて保存（他キー・順序は保持）。"""
    path = _config_local_path()
    if not path.exists():
        raise FileNotFoundError("config.local.json が無い（ピンは個人設定に保存する）")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["pinned_sessions"] = pins
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _dormant_row(sid, summary, mtime, ws, ws_labels, note):
    return {
        "sid": sid,
        "sid_short": sid[:8],
        "project": summary.get("project") or "—",
        "s_no": f"S{summary['session_no']}" if summary.get("session_no") is not None else "",
        "title": summary.get("first_user_text") or summary.get("ai_title") or sid[:8],
        "last_age": _age(epoch_ms=mtime * 1000),
        "last_epoch": mtime,
        "note": note or "",
        "ws": _ci_get(ws_labels, ws, ws[:16]),
    }


def _dormant_rows(cfg, claude_dir, live_sids, pins, mapper, ws_labels, now_epoch):
    """再開予定（ピン済み・プロセス停止中）と再開候補（自動提示）の2リストを返す。"""
    lookback = cfg.get("dormant_lookback_days", 10) * 86400
    suggest_max = cfg.get("dormant_suggest_max", 8)
    pin_by_sid = {p["sid"]: p for p in pins}

    files = [(s, jf, ws, jf.stat().st_mtime) for s, jf, ws in _iter_session_files(claude_dir)]

    # 📌 再開予定: ピン済みかつプロセス停止中（稼働中ならメインボードに出るので除外）。年齢無関係。
    pinned_rows = []
    for s, jf, ws, mtime in files:
        if s in pin_by_sid and s not in live_sids:
            summary = _peek(jf, ws)
            mapper.map_session(summary)
            pinned_rows.append(_dormant_row(s, summary, mtime, ws, ws_labels,
                                            pin_by_sid[s].get("note", "")))
    pinned_rows.sort(key=lambda r: -r["last_epoch"])

    # 💤 再開候補: プロセス停止中・直近lookback内更新・未ピン・案件マップ済・会話実体あり。
    # peek はコストがあるので mtime 降順で上位だけ舐める（表示数の数倍で足切り）。
    cutoff = now_epoch - lookback
    cand = sorted((f for f in files
                   if f[0] not in live_sids and f[0] not in pin_by_sid and f[3] >= cutoff),
                  key=lambda x: -x[3])
    suggest_rows = []
    for s, jf, ws, mtime in cand[:max(suggest_max * 4, 12)]:
        summary = _peek(jf, ws)
        mapper.map_session(summary)
        if not summary.get("project") or not summary.get("first_user_text"):
            continue
        suggest_rows.append(_dormant_row(s, summary, mtime, ws, ws_labels, ""))
        if len(suggest_rows) >= suggest_max:
            break
    return pinned_rows, suggest_rows


def _peek(transcript: Path, workspace: str):
    """冒頭だけ読んで採番マッチ用の擬似サマリーを作る（全パースしない）。"""
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
                    msg = rec.get("message") or {}
                    for raw in _iter_text_items(msg.get("content")):
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


def _age(ts_iso=None, epoch_ms=None):
    now = datetime.now(timezone.utc)
    dt = None
    if ts_iso:
        try:
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        except ValueError:
            pass
    elif epoch_ms:
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    if not dt:
        return "?"
    sec = int((now - dt).total_seconds())
    if sec < 60:
        return f"{sec}秒前"
    if sec < 3600:
        return f"{sec // 60}分前"
    if sec < 86400:
        return f"{sec // 3600}時間前"
    return f"{sec // 86400}日前"


def _dur(sec):
    """経過秒 → 簡潔な継続時間表記（'N秒'/'N分'/'N時間'/'N日'）。待ち時間の表示用。"""
    sec = int(sec)
    if sec < 60:
        return f"{sec}秒"
    if sec < 3600:
        return f"{sec // 60}分"
    if sec < 86400:
        return f"{sec // 3600}時間"
    return f"{sec // 86400}日"


def _updated_epoch(ts_iso=None, epoch_ms=None):
    """ソート用の更新時刻（epoch秒・新しいほど大）。状態JSONのts優先、無ければstartedAt。"""
    if ts_iso:
        try:
            return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if epoch_ms:
        return epoch_ms / 1000
    return 0.0


def _trim(s, limit):
    s = (s or "").replace("\n", " ")
    return s[:limit] + ("…" if len(s) > limit else "")


def _w(s):
    """表示幅（全角=2）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in s)


def _pad(s, width):
    """表示幅ベースの左寄せパディング（超過分は切り詰め）。"""
    while _w(s) > width - 1:
        s = s[:-1]
    return s + " " * (width - _w(s))


def _ci_get(d, key, default=None):
    """辞書のケースインセンシティブ引き（ドライブレター大文字/小文字ズレ対策）。"""
    if key in d:
        return d[key]
    kl = key.lower()
    return next((v for k, v in d.items() if k.lower() == kl), default)


def build_rows():
    cfg = load_config(REPO_ROOT)
    claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
    ws_labels = cfg.get("workspace_labels", {})
    mapper = ProjectMapper(cfg)

    agents = _agents_json()
    states = _load_states(claude_dir)
    pins = _load_pins(cfg)
    pin_sids = {p["sid"] for p in pins}
    fresh_sec = cfg.get("fresh_working_sec", 30)
    new_wait_sec = cfg.get("new_waiting_sec", 180)  # 待ち入りからこの秒数以内は「新着」🆕
    now_epoch = datetime.now(timezone.utc).timestamp()

    if agents is None:
        # claude CLI が使えない場合は直近24hの状態ファイルだけで表示
        print("⚠️ claude agents --json が実行できないため状態ファイルのみで表示")
        agents = {}
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        for sid, st in states.items():
            try:
                ts = datetime.fromisoformat(st["ts"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                continue
            if ts >= cutoff:
                agents[sid] = {"sessionId": sid, "cwd": st.get("cwd", ""), "pid": None}

    rows = []
    # transcript(jsonl)が存在しないセッションは除外する。実体は Cursor がウィンドウ復元時に
    # 1タブにつき2プロセス起動する（まっさらな fresh 起動 → 直後に --resume 本体）ことで生じる
    # 「fresh側の捨てプロセス」。会話が1度も無いため <sid>.jsonl が生成されないが、プロセスは
    # 生き続けて claude agents --json に出続ける。本体リストからは消し、件数だけ脚注に出す。
    skipped = 0
    for sid, rec in agents.items():
        st = states.get(sid, {})
        cwd = rec.get("cwd") or st.get("cwd") or ""
        ws = _enc_ws(cwd)
        # ドライブレターの大小ズレを config のキー表記に正規化
        ws = next((k for k in list(ws_labels) + list(mapper.workspace_hints)
                   if k.lower() == ws.lower()), ws)
        cand = [claude_dir / "projects" / ws / f"{sid}.jsonl"]
        if st.get("transcript_path"):
            cand.append(Path(st["transcript_path"]))
        transcript = next((p for p in cand if p.exists()), None)
        if transcript is None:
            skipped += 1
            continue
        summary = _peek(transcript, ws)
        mapper.map_session(summary)

        # 状態判定は resolve_state に集約（hook状態=ターン境界を主軸／jsonl mtimeはフォールバック）。
        # S6で session_notify を python化しCursorでもフックが発火するようになったため、S5の
        # 「jsonl mtime 主軸」から「hook状態 主軸＋jsonl フォールバック」へ。長いターンの谷間で
        # 🟡へ誤落ちする問題（S7・ふくちゃん報告）を解消。
        mtime = transcript.stat().st_mtime
        hk_ts_epoch = None
        if st.get("ts"):
            try:
                hk_ts_epoch = datetime.fromisoformat(st["ts"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        state = resolve_state(now_epoch, mtime, st.get("state"), hk_ts_epoch, fresh_sec)

        # 待ち状態の「待ち時間」と「新着」判定（A案・S7 ふくちゃん要望＝未確認/確認済みの近似）。
        # 待ちに入った時刻＝hook ts（Stop/Notification 発火時）。無ければ jsonl mtime で近似。
        # 待ち入りから new_wait_sec 秒以内＝🆕新着（≒トーストが出たばかりで未確認の可能性大）。
        wait_sec = None
        if state in ("waiting_input", "waiting_permission"):
            since = hk_ts_epoch if hk_ts_epoch is not None else mtime
            wait_sec = max(0, int(now_epoch - since))
        is_new = wait_sec is not None and wait_sec < new_wait_sec

        title = (st.get("title") or summary["first_user_text"] or rec.get("name")
                 or (Path(cwd).name if cwd else "?"))
        rows.append({
            "state": state,
            "project": summary["project"] or "—",
            "s_no": f"S{summary['session_no']}" if summary.get("session_no") is not None else "",
            "title": title,
            "updated": _age(epoch_ms=mtime * 1000),
            "updated_epoch": mtime,
            "wait_sec": wait_sec,
            "is_new": is_new,
            "pinned": sid in pin_sids,  # 稼働中のピン済みは📌印を付ける（休眠セクションには出さない）
            "ws": _ci_get(ws_labels, ws, Path(cwd).name if cwd else ws[:16]),
            "pid": rec.get("pid") or "",
        })
    # 待ち状態（🔴許可待ち→🟡確認待ち）を最上部に固定し、各グループ内は更新の新しい順。
    # 作業中も新しい順＝いま動いているセッションが上に来る（見逃し防止と「今どれ」を両立）。
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9), -r["updated_epoch"]))

    # 休眠セッション（プロセス停止中）の再開予定／再開候補（S7）
    live_sids = set(agents.keys())  # プロセスが生きているものは休眠から除外（幽霊含め再開提案しない）
    pinned_rows, suggest_rows = _dormant_rows(
        cfg, claude_dir, live_sids, pins, mapper, ws_labels, now_epoch)
    return rows, skipped, pinned_rows, suggest_rows


def render(rows, skipped=0, pinned_rows=None, suggest_rows=None):
    now = datetime.now().strftime("%H:%M:%S")
    n_wait = sum(1 for r in rows if r["state"].startswith("waiting"))
    print(f"🖥️ Claude ライブボード {now} — 稼働 {len(rows)} セッション（うち待ち {n_wait}）\n")
    if not rows:
        print("（稼働中セッションなし）")
    else:
        print(_pad("状態", 18) + _pad("案件", 22) + _pad("S", 5) + _pad("更新", 10)
              + _pad("WS", 16) + _pad("pid", 7) + "タイトル")
        print("-" * 116)
        for r in rows:
            state = STATE_LABELS.get(r["state"], "⚪ 不明")
            if r.get("wait_sec") is not None:      # 待ち時間を状態に併記
                state += f" {_dur(r['wait_sec'])}"
            if r.get("is_new"):                    # 新着（待ち入りたて＝未確認の可能性大）
                state = "🆕" + state
            project = ("📌" + r["project"]) if r.get("pinned") else r["project"]
            print(_pad(state, 18) + _pad(project, 22) + _pad(r["s_no"], 5)
                  + _pad(r["updated"], 10) + _pad(r["ws"], 16) + _pad(str(r["pid"]), 7)
                  + _trim(r["title"], 50))
    if skipped:
        print(f"\n※ 非表示: 会話実体のない空セッション {skipped}件"
              "（Cursor復元時の重複起動プロセス）")
    _render_dormant(pinned_rows or [], suggest_rows or [])


def _render_dormant(pinned_rows, suggest_rows):
    """休眠セッション（プロセス停止中）の再開予定／再開候補をターミナルに表示。"""
    def _line(r, with_note):
        note = f"  — {r['note']}" if (with_note and r["note"]) else ""
        return ("  " + _pad(r["sid_short"], 9) + _pad(r["project"], 22) + _pad(r["s_no"], 5)
                + _pad("最終 " + r["last_age"], 13) + _trim(r["title"], 40) + note)
    if pinned_rows:
        print("\n📌 再開予定（ピン済み・プロセス停止中）")
        for r in pinned_rows:
            print(_line(r, True))
    if suggest_rows:
        print("\n💤 再開候補（案件マップ済・直近更新・未ピン）"
              "  ← 続けるなら: python live_board.py pin <sid>")
        for r in suggest_rows:
            print(_line(r, False))


STATE_COLORS = {
    "working": ("#27ae60", "作業中"),
    "waiting_input": ("#f39c12", "待機中"),
    "waiting_permission": ("#c0392b", "許可待ち"),
}


def _dormant_html(pinned_rows, suggest_rows):
    """休眠セッションの再開予定／再開候補を HTML セクションとして返す。"""
    esc = html_mod.escape

    def _rows_html(rows, with_note):
        out = []
        for r in rows:
            note = (f'<span class="dnote">— {esc(r["note"])}</span>'
                    if (with_note and r["note"]) else '')
            out.append(
                f'<tr><td class="sid">{esc(r["sid_short"])}</td>'
                f'<td class="pj">{esc(r["project"])}</td><td>{esc(r["s_no"])}</td>'
                f'<td>最終 {esc(r["last_age"])}</td>'
                f'<td class="title">{esc(_trim(r["title"], 60))} {note}</td></tr>')
        return "".join(out)

    sections = []
    if pinned_rows:
        sections.append(
            '<h2>📌 再開予定 <span class="sub">（ピン済み・プロセス停止中）</span></h2>'
            f'<table class="dormant">{_rows_html(pinned_rows, True)}</table>')
    if suggest_rows:
        sections.append(
            '<h2>💤 再開候補 <span class="sub">（案件マップ済・直近更新・未ピン）'
            '　続けるなら <code>python live_board.py pin &lt;sid&gt;</code></span></h2>'
            f'<table class="dormant">{_rows_html(suggest_rows, False)}</table>')
    return "".join(sections)


def render_html(rows, refresh_sec, skipped=0, pinned_rows=None, suggest_rows=None):
    esc = html_mod.escape
    now = datetime.now().strftime("%H:%M:%S")
    n_wait = sum(1 for r in rows if r["state"].startswith("waiting"))
    footnote = (f'<div class="note">※ 非表示: 会話実体のない空セッション {skipped}件'
                "（Cursor復元時の重複起動プロセス）</div>") if skipped else ""
    body = []
    for r in rows:
        color, label = STATE_COLORS.get(r["state"], ("#95a5a6", "不明"))
        if r.get("wait_sec") is not None:      # 待ち時間を状態に併記
            label += f" {_dur(r['wait_sec'])}"
        newbadge = '<span class="new">🆕</span>' if r.get("is_new") else ''  # 新着＝未確認の目印
        pin = '<span class="pin">📌</span>' if r.get("pinned") else ''  # 稼働中のピン済み
        body.append(
            f'<tr class="{"newrow" if r.get("is_new") else ""}">'
            f'<td>{newbadge}<span class="badge" style="background:{color}">{label}</span></td>'
            f'<td class="pj">{pin}{esc(r["project"])}</td><td>{esc(r["s_no"])}</td>'
            f'<td>{esc(r["updated"])}</td><td>{esc(r["ws"])}</td>'
            f'<td class="title">{esc(_trim(r["title"], 70))}</td></tr>')
    dormant = _dormant_html(pinned_rows or [], suggest_rows or [])
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_sec}">
<title>Claude ライブボード</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Yu Gothic", "Meiryo", sans-serif;
         margin: 0; padding: 20px; background: #f4f6f8; color: #1c2833; }}
  h1 {{ font-size: 17px; margin: 0 0 2px; }}
  .meta {{ color: #888; font-size: 12px; margin-bottom: 14px; }}
  table {{ border-collapse: collapse; background: #fff; border-radius: 8px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); font-size: 13px; width: 100%; }}
  th {{ text-align: left; color: #666; font-weight: normal; padding: 8px 12px;
        border-bottom: 1px solid #e3e8ec; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #f0f3f5; }}
  .badge {{ color: #fff; border-radius: 10px; padding: 2px 10px; font-size: 12px;
            white-space: nowrap; }}
  .pj {{ font-weight: bold; }}
  .title {{ color: #666; }}
  .note {{ color: #999; font-size: 12px; margin-top: 10px; }}
  .new {{ margin-right: 5px; }}
  .newrow {{ background: #fffbe6; }}
  .pin {{ margin-right: 4px; }}
  h2 {{ font-size: 14px; margin: 22px 0 6px; color: #555; }}
  h2 .sub {{ font-weight: normal; font-size: 12px; color: #999; }}
  h2 code {{ background: #eef1f4; padding: 1px 5px; border-radius: 4px; font-size: 11px; }}
  table.dormant {{ font-size: 12px; }}
  table.dormant .sid {{ font-family: ui-monospace, "Consolas", monospace; color: #999; }}
  .dnote {{ color: #b7791f; }}
</style>
</head>
<body>
<h1>🖥️ Claude ライブボード — 稼働 {len(rows)} セッション（うち待ち {n_wait}）</h1>
<div class="meta">生成 {now} ／ {refresh_sec}秒ごと自動更新（裏で live_board.py --html --watch が動いている前提）</div>
<table>
<tr><th>状態</th><th>案件</th><th>S</th><th>更新</th><th>WS</th><th>タイトル</th></tr>
{"".join(body)}
</table>
{footnote}
{dormant}
</body>
</html>"""


def _kill_stale_watchers():
    """多重起動した他の live_board 監視プロセスを停止する（出力ファイルの奪い合い防止）。Windows専用。
    自PID以外で live_board.py を実行中の python.exe が対象。PID再利用による誤爆を避けるため、
    コマンドラインに 'live_board.py' を含むものだけ落とす。旧コードを抱えた watcher が残って
    古い状態で HTML を上書きし続ける事故（S5 2026-07-30）への恒久対策。"""
    if os.name != "nt":
        return 0
    ps = ("$me=%d; $t=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*live_board.py*' -and $_.ProcessId -ne $me }); "
          "$t | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
          "$t.Count") % os.getpid()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
        return int((r.stdout or "0").strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return 0


def _cmd_pins(cfg, claude_dir):
    pins = _load_pins(cfg)
    if not pins:
        print("📌 ピンなし（python live_board.py pin <sid接頭辞> [メモ] で追加）")
        return
    print(f"📌 ピン済み {len(pins)}件:")
    for p in pins:
        jf, _ = _find_by_sid(claude_dir, p["sid"])
        warn = "" if jf else "  ⚠️jsonl見つからず"
        note = f"  — {p['note']}" if p.get("note") else ""
        print(f"  {p['sid'][:12]}…  {p['sid']}{note}{warn}")


def _cmd_pin(cfg, claude_dir, prefix, note):
    sid, err = _resolve_sid_prefix(claude_dir, prefix)
    if err:
        print("✗ " + err)
        return
    pins = _load_pins(cfg)
    if any(p["sid"] == sid for p in pins):
        print(f"すでにピン済み: {sid[:12]}…")
        return
    pins.append({"sid": sid, "note": note,
                 "pinned_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    _save_pins(pins)
    print(f"📌 ピン追加: {sid[:12]}…" + (f"  — {note}" if note else ""))


def _cmd_unpin(cfg, claude_dir, prefix):
    pins = _load_pins(cfg)
    matches = [p for p in pins if p["sid"].startswith(prefix)]
    if len(matches) != 1:
        detail = "該当なし" if not matches else f"{len(matches)}件マッチ・接頭辞を伸ばして"
        print(f"✗ ピン解除対象が特定できない（{detail}）")
        return
    sid = matches[0]["sid"]
    _save_pins([p for p in pins if p["sid"] != sid])
    print(f"🗑 ピン解除: {sid[:12]}…")


def main():
    args = sys.argv[1:]

    # 休眠セッションのピン操作 CLI（B案の芯＝人の意図を config.local に保存する）
    if args and args[0] in ("pin", "unpin", "pins"):
        cfg = load_config(REPO_ROOT)
        claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
        cmd = args[0]
        if cmd == "pins":
            _cmd_pins(cfg, claude_dir)
        elif cmd == "pin":
            if len(args) < 2:
                print("使い方: python live_board.py pin <sid接頭辞> [メモ]")
            else:
                _cmd_pin(cfg, claude_dir, args[1], " ".join(args[2:]))
        elif cmd == "unpin":
            if len(args) < 2:
                print("使い方: python live_board.py unpin <sid接頭辞>")
            else:
                _cmd_unpin(cfg, claude_dir, args[1])
        return

    watch = None
    if "--watch" in args:
        i = args.index("--watch")
        watch = int(args[i + 1]) if len(args) > i + 1 and args[i + 1].isdigit() else 10
    html_mode = "--html" in args
    if watch:  # 常駐は1個だけに保つ（古いコードの watcher が残って上書きし返す事故を防ぐ）
        n = _kill_stale_watchers()
        if n:
            print(f"既存の監視プロセス {n}件を停止（多重起動の掃除）")
    out_path = REPO_ROOT / "reports" / "live_board.html"
    while True:
        rows, skipped, pinned_rows, suggest_rows = build_rows()
        if html_mode:
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_text(
                render_html(rows, watch or 10, skipped, pinned_rows, suggest_rows),
                encoding="utf-8")
            print(f"{datetime.now().strftime('%H:%M:%S')} updated -> {out_path}")
        else:
            if watch:
                os.system("cls" if os.name == "nt" else "clear")
            render(rows, skipped, pinned_rows, suggest_rows)
        if not watch:
            break
        time.sleep(watch)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        pass
