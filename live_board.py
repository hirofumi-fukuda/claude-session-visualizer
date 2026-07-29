"""並行セッションのライブボード（照準②の最小ビュー）。

データ源の3点合成:
  1. claude agents --json                 … 稼働中セッション一覧（公式・pid/cwd/sessionId）
  2. ~/.claude/hooks/session_state/*.json … session_notify.ps1 が書く状態
     （working=作業中 / waiting_input=確認待ち / waiting_permission=許可待ち）
  3. claude_sv.mapping                    … 採番規約→案件名の解決（冒頭発話をピーク読み）

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
    "waiting_input": "🟡 確認待ち",
    "waiting_permission": "🔴 許可待ち",
}
STATE_ORDER = {"waiting_permission": 0, "waiting_input": 1, "working": 2}


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
        summary = _peek(transcript, ws) if transcript else {
            "ai_title": None, "first_user_text": None, "last_prompt": None,
            "user_texts_matchpool": [], "workspace": ws}
        mapper.map_session(summary)

        state = st.get("state") or ({"waiting": "waiting_input"}.get(rec.get("status"), ""))
        title = (st.get("title") or summary["first_user_text"] or rec.get("name")
                 or (Path(cwd).name if cwd else "?"))
        rows.append({
            "state": state,
            "project": summary["project"] or "—",
            "s_no": f"S{summary['session_no']}" if summary.get("session_no") is not None else "",
            "title": title,
            "updated": _age(st.get("ts")) if st.get("ts") else _age(epoch_ms=rec.get("startedAt")),
            "updated_epoch": _updated_epoch(st.get("ts"), rec.get("startedAt")),
            "ws": _ci_get(ws_labels, ws, Path(cwd).name if cwd else ws[:16]),
            "pid": rec.get("pid") or "",
        })
    # 待ち状態（🔴許可待ち→🟡確認待ち）を最上部に固定し、各グループ内は更新の新しい順。
    # 作業中も新しい順＝いま動いているセッションが上に来る（見逃し防止と「今どれ」を両立）。
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9), -r["updated_epoch"]))
    return rows


def render(rows):
    now = datetime.now().strftime("%H:%M:%S")
    n_wait = sum(1 for r in rows if r["state"].startswith("waiting"))
    print(f"🖥️ Claude ライブボード {now} — 稼働 {len(rows)} セッション（うち待ち {n_wait}）\n")
    if not rows:
        print("（稼働中セッションなし）")
        return
    print(_pad("状態", 12) + _pad("案件", 22) + _pad("S", 5) + _pad("更新", 10)
          + _pad("WS", 16) + _pad("pid", 7) + "タイトル")
    print("-" * 110)
    for r in rows:
        state = STATE_LABELS.get(r["state"], "⚪ 不明")
        print(_pad(state, 12) + _pad(r["project"], 22) + _pad(r["s_no"], 5)
              + _pad(r["updated"], 10) + _pad(r["ws"], 16) + _pad(str(r["pid"]), 7)
              + _trim(r["title"], 50))


STATE_COLORS = {
    "working": ("#27ae60", "作業中"),
    "waiting_input": ("#f39c12", "確認待ち"),
    "waiting_permission": ("#c0392b", "許可待ち"),
}


def render_html(rows, refresh_sec):
    esc = html_mod.escape
    now = datetime.now().strftime("%H:%M:%S")
    n_wait = sum(1 for r in rows if r["state"].startswith("waiting"))
    body = []
    for r in rows:
        color, label = STATE_COLORS.get(r["state"], ("#95a5a6", "不明"))
        body.append(
            f'<tr><td><span class="badge" style="background:{color}">{label}</span></td>'
            f'<td class="pj">{esc(r["project"])}</td><td>{esc(r["s_no"])}</td>'
            f'<td>{esc(r["updated"])}</td><td>{esc(r["ws"])}</td>'
            f'<td class="title">{esc(_trim(r["title"], 70))}</td></tr>')
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
</style>
</head>
<body>
<h1>🖥️ Claude ライブボード — 稼働 {len(rows)} セッション（うち待ち {n_wait}）</h1>
<div class="meta">生成 {now} ／ {refresh_sec}秒ごと自動更新（裏で live_board.py --html --watch が動いている前提）</div>
<table>
<tr><th>状態</th><th>案件</th><th>S</th><th>更新</th><th>WS</th><th>タイトル</th></tr>
{"".join(body)}
</table>
</body>
</html>"""


def main():
    args = sys.argv[1:]
    watch = None
    if "--watch" in args:
        i = args.index("--watch")
        watch = int(args[i + 1]) if len(args) > i + 1 and args[i + 1].isdigit() else 10
    html_mode = "--html" in args
    out_path = REPO_ROOT / "reports" / "live_board.html"
    while True:
        rows = build_rows()
        if html_mode:
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_text(render_html(rows, watch or 10), encoding="utf-8")
            print(f"{datetime.now().strftime('%H:%M:%S')} updated -> {out_path}")
        else:
            if watch:
                os.system("cls" if os.name == "nt" else "clear")
            render(rows)
        if not watch:
            break
        time.sleep(watch)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        pass
