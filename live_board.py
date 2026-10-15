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
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from claude_sv.ledger import slug
from claude_sv.mapping import ProjectMapper, load_config
from claude_sv.modelbadge import MDL_CSS, model_badge
from claude_sv.daily_view import write_daily
from claude_sv.messages import collect as collect_messages, label_sessions, load_registry, recent_html
from claude_sv.office import write_office
from claude_sv.nav import nav_html
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
    いた＝ユーザー報告のバグ）。jsonl mtime は hook 状態が無いセッション（フック配線前 等）の
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


_CLI_CACHE = []  # _claude_cli の解決結果（プロセス内で1度だけ探す）

# 拡張版CCが claude 実行ファイルを同梱している場所（バージョン番号付き＝glob で最新を拾う）
_CLI_GLOBS = [
    ".cursor/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe",
    ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe",
    ".vscode-insiders/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe",
    ".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    ".claude/local/claude.exe",
    ".claude/local/claude",
]


def _probe_cli(exe):
    """候補が「実在するのに壊れている」ことがある（PATH の Volta/npm shim の実体が
    自動更新失敗で消失＝2026-08-10 実地）ため、--version が通るものだけを採用する。"""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _claude_cli(cfg=None):
    """claude 実行ファイルのパスを解決する（config → PATH → 素CLI標準 → 拡張同梱バイナリ）。

    Cursor/VSCode 拡張版CCは claude 本体を拡張ディレクトリ内に同梱し PATH には置かない。
    素のCLIを別途入れていない環境では `claude agents --json` が command not found になり、
    ライブボードが「状態ファイルのみ」の縮退表示に落ちる。ディレクトリ名にバージョン番号が
    入るので glob で拾い、複数あれば mtime が新しい方＝現行版を使う（拡張更新で壊れない）。
    config.local.json の `claude_cli` に絶対パスを書けばそれを最優先。
    候補は --version プローブが通った最初のものをプロセス内キャッシュ（壊れ候補スキップ）。
    """
    if _CLI_CACHE:
        return _CLI_CACHE[0]
    cands = []
    exe = (cfg or {}).get("claude_cli") or ""
    if exe and Path(exe).exists():
        cands.append(exe)
    w = shutil.which("claude")
    if w:
        cands.append(w)
    home = Path.home()
    native = home / ".local" / "bin" / "claude.exe"  # 公式スタンドアロン版の標準配置
    if native.is_file():
        cands.append(str(native))
    found = [p for g in _CLI_GLOBS for p in home.glob(g) if p.is_file()]
    if found:
        cands.append(str(max(found, key=lambda p: p.stat().st_mtime)))
    _CLI_CACHE.append(next((c for c in cands if _probe_cli(c)), None))
    return _CLI_CACHE[0]


def _agents_json(cfg=None):
    """claude agents --json で稼働中セッション一覧を取得（sessionId 重複は新しい方を残す）。"""
    exe = _claude_cli(cfg)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "agents", "--json"], capture_output=True,
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


def _ts_epoch(ts_iso):
    """UTC ISO(Z) → epoch秒。壊れていたら None。"""
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ─── 委託ジョブ（エージェントグラフ Phase 1）───────────────────────────────
# 「👋自分待ち／⚙️自分が動く」に「⏳委託が動く」を足す。Claude セッションではない
# ローカルLLMバッチ等は agents --json にも session_state にも出ない＝死んでも気づけない。
# agent-graph/agent_job.py が hooks/agent_jobs/<job_id>.json を書き、ここで読む。

def _load_jobs(claude_dir: Path, now_epoch, recent_hours=24):
    """委託ジョブ台帳を読み、running と直近の done/failed の2リストを返す。

    running の stale ＝最終更新(ts)が stale_sec 以上途絶＝ジョブ死亡の疑い（⚠️強調）。
    done/failed は recent_hours 時間だけ出す（完了・失敗の見逃し防止。掃除は agent_job 側）。
    """
    jobs_dir = claude_dir / "hooks" / "agent_jobs"
    running, finished = [], []
    if not jobs_dir.is_dir():
        return running, finished
    for p in jobs_dir.glob("*.json"):
        # 台帳は複数ライター前提の共有ディレクトリ。壊れたファイル（dictでないJSON・
        # 型のおかしい値・読んだ直後の削除）が1個あっても watcher を殺さない＝
        # 1ファイル分の処理全体を握って読み飛ばす（死んだジョブの検知装置自身が死なないこと）。
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            ts_e = _ts_epoch(j.get("ts")) or p.stat().st_mtime
            started_e = _ts_epoch(j.get("started")) or ts_e
            upd_sec = max(0, int(now_epoch - ts_e))
            total = int(j["total"]) if j.get("total") else None
            cur = int(j.get("cur") or 0)
            row = {
                "job_id": j.get("job_id") or p.stem,
                "state": j.get("state") or "?",
                "title": j.get("title") or p.stem,
                "project": j.get("project") or "—",
                "agent": j.get("agent") or "",
                "prog": f"{cur}/{total}" if total else "",
                "pct": min(100, round(cur * 100 / total)) if total else None,
                "note": j.get("note") or "",
                "summary": j.get("summary") or "",
                "elapsed": _dur(max(0, now_epoch - started_e)),
                "upd_age": _age(epoch_ms=ts_e * 1000),
                "upd_sec": upd_sec,
                "stale": upd_sec > int(j.get("stale_sec") or 1800),
                "ts_epoch": ts_e,
                "started_epoch": started_e,
            }
        except Exception:
            continue
        if row["state"] == "running":
            running.append(row)
        elif row["state"] in ("done", "failed") and ts_e >= now_epoch - recent_hours * 3600:
            finished.append(row)
    # ⚠️応答なし を先頭に固定、次いで開始の古い順（長く走っているものが上）
    running.sort(key=lambda r: (0 if r["stale"] else 1, r["started_epoch"]))
    finished.sort(key=lambda r: -r["ts_epoch"])
    return running, finished


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


def _dormant_rows(cfg, claude_dir, live_sids, live_proj_max, pins, mapper, ws_labels, now_epoch):
    """再開予定（ピン済み・プロセス停止中）と再開候補（自動提示）の2リストを返す。

    再開候補の抑制ルール（S7・ユーザー方針）:
      - 番号なし単独セッション … 地図（採番）で追えず埋もれやすいので長め（unnumbered）に遡る。
      - 番号付きセッション … 案件ごとの最新Sのみ候補（旧番号は除外）。稼働中に新しいSがあれば
        （live_proj_max）停止中の旧番号は自動的に落ちる＝「S6動いてるならS4/S5は出さない」。
    """
    lb_num = cfg.get("dormant_lookback_days", 10) * 86400
    lb_unnum = cfg.get("dormant_lookback_days_unnumbered", 30) * 86400
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

    # 💤 再開候補: プロセス停止中・未ピン・案件マップ済。番号有無で遡り日数を変える。
    # peek はコストがあるので、広い方（unnumbered）の窓に入るものだけを mtime 降順で舐める。
    cutoff = now_epoch - max(lb_num, lb_unnum)
    cand = sorted((f for f in files
                   if f[0] not in live_sids and f[0] not in pin_by_sid and f[3] >= cutoff),
                  key=lambda x: -x[3])[:max(suggest_max * 8, 50)]
    parsed = []
    for s, jf, ws, mtime in cand:
        summary = _peek(jf, ws)
        mapper.map_session(summary)
        if summary.get("project"):
            parsed.append((s, summary, mtime, ws))

    # 案件ごとの最新S（稼働中＋候補を合算）＝番号付きの旧番号除外の基準
    proj_max = dict(live_proj_max)
    for _s, summary, _m, _w in parsed:
        n = summary.get("session_no")
        if n is not None:
            proj_max[summary["project"]] = max(proj_max.get(summary["project"], -1), n)

    # 2グループに分けて各々を独立枠で出す（番号なしが番号付き最新Sを押し出さないように）
    suggest_unnum, suggest_num = [], []
    for s, summary, mtime, ws in parsed:
        n = summary.get("session_no")
        age = now_epoch - mtime
        if n is not None:  # 番号付き: 案件の最新Sのみ・短lookback
            if age > lb_num or n < proj_max.get(summary["project"], n):
                continue
            suggest_num.append(_dormant_row(s, summary, mtime, ws, ws_labels, ""))
        else:              # 番号なし単独: 会話実体あり・長lookback
            if age > lb_unnum or not summary.get("first_user_text"):
                continue
            suggest_unnum.append(_dormant_row(s, summary, mtime, ws, ws_labels, ""))
    suggest_unnum.sort(key=lambda r: -r["last_epoch"])
    suggest_num.sort(key=lambda r: -r["last_epoch"])
    return pinned_rows, suggest_unnum[:suggest_max], suggest_num[:suggest_max]


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


_LAST_HUMAN_CACHE: dict = {}  # transcript path → (size, result)。ファイルが伸びた時だけ再走査する
_LAST_HUMAN_WINDOWS = (65536, 1 << 20, 1 << 23)  # 末尾 64KB → 1MB → 8MB と広げて探す（長い作業ターンの後は数MB進む）
_NOT_HUMAN_PREFIXES = ("<cross-session-message", "[Subagent hand-back]",
                       "This session is being continued from a previous conversation")


def _peek_last_human(transcript: Path, tail_bytes=None):
    """末尾から読んで「人間が最後に打った発話」の時刻(epoch)と先頭80字を返す（無ければ None）。

    オフィス配置図の「人間がいま注意を向けている席」判定用（S21・ハブ案）。他セッション便
    （cross-session-message＝isMeta）・サブエージェント戻り・tool_result・スラッシュコマンド
    （command-name タグ＝_clean_user_text で空になる）・コンパクション要約（"This session is being
    continued…"＝system が user 役で挿入）は人間の発話に数えない。
    """
    key = str(transcript)
    try:
        size = transcript.stat().st_size
    except OSError:
        return None
    hit = _LAST_HUMAN_CACHE.get(key)
    if hit and hit[0] == size:
        return hit[1]
    windows = (tail_bytes,) if tail_bytes else _LAST_HUMAN_WINDOWS
    result = None
    for tb in windows:
        try:
            with open(transcript, "rb") as f:
                f.seek(max(0, size - tb))
                chunk = f.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        result = _scan_last_human(chunk)
        if result or tb >= size:
            break
    _LAST_HUMAN_CACHE[key] = (size, result)
    return result


def _scan_last_human(chunk: str):
    for line in reversed(chunk.splitlines()):
        if '"type":"user"' not in line or '"isMeta":true' in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
            continue
        content = (rec.get("message") or {}).get("content")
        for raw in _iter_text_items(content):
            text = _clean_user_text(raw)
            if not text or text.startswith(_NOT_HUMAN_PREFIXES):
                continue
            ts = _ts_epoch(rec.get("timestamp"))
            if ts:
                return {"ts": ts, "text": text[:80]}
        # AskUserQuestion の回答（10/15 S21 見直し⑤）: ボタン回答は user テキストでなく tool_result
        # （"Your questions have been answered: ..."）で残る。実際に人が答えているので発話に数える
        ans = _ask_answer_text(content)
        if ans:
            ts = _ts_epoch(rec.get("timestamp"))
            if ts:
                return {"ts": ts, "text": ans[:80]}
    return None


_ASK_ANSWER_PREFIX = "Your questions have been answered:"


def _ask_answer_text(content):
    """user レコードの tool_result から AskUserQuestion の回答本文（"質問"="回答", ...）を取り出す。無ければ ''。"""
    if not isinstance(content, list):
        return ""
    for item in content:
        if not (isinstance(item, dict) and item.get("type") == "tool_result"):
            continue
        body = item.get("content")
        if isinstance(body, list):
            body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
        if not isinstance(body, str) or not body.startswith(_ASK_ANSWER_PREFIX):
            continue
        body = body[len(_ASK_ANSWER_PREFIX):].strip()
        body = re.sub(r"\.?\s*You can now continue with these answers in mind\.?$", "", body).strip()
        return "🔘 " + body
    return ""


def _peek_model(transcript: Path, tail_bytes=None):
    """末尾だけ読んで最後の assistant レコードの model を短縮形で返す（委託のengine表記と対）。

    /model で途中切替されるためヘッダではなく末尾が正。先頭ピーク(_peek)と対称の軽量読み。
    例: claude-fable-5 → fable-5 / claude-haiku-4-5-20251001 → haiku-4-5
    末尾 64KB に assistant 行が無い（巨大な tool_result 1行の直後など）と「モデル不明」になるので、
    _peek_last_human と同じく 64KB → 1MB → 8MB と広げて探す（S21・オフィスの席が一時的に赤くなった件）。
    """
    try:
        size = transcript.stat().st_size
    except OSError:
        return ""
    for tb in ((tail_bytes,) if tail_bytes else _LAST_HUMAN_WINDOWS):
        try:
            with open(transcript, "rb") as f:
                f.seek(max(0, size - tb))
                chunk = f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        model = _scan_model(chunk)
        if model or tb >= size:
            return model
    return ""


def _scan_model(chunk: str) -> str:
    for line in reversed(chunk.splitlines()):
        if '"model"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # シーク位置で先頭行が欠けている場合など
        model = (rec.get("message") or {}).get("model") or ""
        if model and model != "<synthetic>":
            return re.sub(r"-20\d{6}$", "", re.sub(r"^claude-", "", model))
    return ""


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

    agents = _agents_json(cfg)
    states = _load_states(claude_dir)
    # /rename 名は claude agents --json に nameSource が無いので稼働台帳 sessions/*.json から（S23）
    registry = load_registry(claude_dir)
    pins = _load_pins(cfg)
    pin_sids = {p["sid"] for p in pins}
    fresh_sec = cfg.get("fresh_working_sec", 30)
    new_wait_sec = cfg.get("new_waiting_sec", 180)  # 待ち入りからこの秒数以内は「新着」🆕
    now_epoch = datetime.now(timezone.utc).timestamp()

    degraded = agents is None  # 縮退＝稼働/休眠の区別が hook 状態の近似になる（画面にも明示する）
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
        reg = registry.get(sid) or {}
        if reg.get("nameSource") == "user" and reg.get("name"):
            summary["user_name"] = reg["name"]  # /rename 名＝案件判定の最優先（mapping ⓪・S23）
        mapper.map_session(summary)

        # 状態判定は resolve_state に集約（hook状態=ターン境界を主軸／jsonl mtimeはフォールバック）。
        # S6で session_notify を python化しCursorでもフックが発火するようになったため、S5の
        # 「jsonl mtime 主軸」から「hook状態 主軸＋jsonl フォールバック」へ。長いターンの谷間で
        # 🟡へ誤落ちする問題（S7・ユーザー報告）を解消。
        mtime = transcript.stat().st_mtime
        hk_ts_epoch = None
        if st.get("ts"):
            try:
                hk_ts_epoch = datetime.fromisoformat(st["ts"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        state = resolve_state(now_epoch, mtime, st.get("state"), hk_ts_epoch, fresh_sec)

        # 待ち状態の「待ち時間」と「新着」判定（A案・S7 ユーザー要望＝未確認/確認済みの近似）。
        # 待ちに入った時刻＝hook ts（Stop/Notification 発火時）。無ければ jsonl mtime で近似。
        # 待ち入りから new_wait_sec 秒以内＝🆕新着（≒トーストが出たばかりで未確認の可能性大）。
        wait_sec = None
        if state in ("waiting_input", "waiting_permission"):
            since = hk_ts_epoch if hk_ts_epoch is not None else mtime
            wait_sec = max(0, int(now_epoch - since))
        is_new = wait_sec is not None and wait_sec < new_wait_sec

        # 概要は first_user_text（parser の全アイテム走査＋タグ除去＝正確）を最優先。
        # hook state の title は resolve_title の旧ロジック汚染（skill展開文
        # 「Base directory for this skill:...」等）が残り得るためフォールバックに降格（S10）。
        title = (summary["first_user_text"] or st.get("title") or rec.get("name")
                 or (Path(cwd).name if cwd else "?"))
        rows.append({
            "state": state,
            "project": summary["project"] or "—",
            "session_no": summary.get("session_no"),
            "s_no": f"S{summary['session_no']}" if summary.get("session_no") is not None else "",
            "title": title,
            "updated": _age(epoch_ms=mtime * 1000),
            "updated_epoch": mtime,
            "wait_sec": wait_sec,
            "is_new": is_new,
            "pinned": sid in pin_sids,  # 稼働中のピン済みは📌印を付ける（休眠セクションには出さない）
            "ws": _ci_get(ws_labels, ws, Path(cwd).name if cwd else ws[:16]),
            "ws_key": ws,  # encoded ws dir（office のテナント＝部屋の判定に使う・S22）
            "model": _peek_model(transcript),  # 委託の engine 表記と対（誰が働いているか）
            "last_human": _peek_last_human(transcript),  # 人間の最終発話（オフィス図の人間位置）
            "pid": rec.get("pid") or "",
            "sid": sid,   # タブ復元スナップショット用（claude-wt/restore_tabs.ps1）
            "cwd": cwd,
        })
    # 待ち状態（🔴許可待ち→🟡確認待ち）を最上部に固定し、各グループ内は更新の新しい順。
    # 作業中も新しい順＝いま動いているセッションが上に来る（見逃し防止と「今どれ」を両立）。
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9), -r["updated_epoch"]))

    # 休眠セッション（プロセス停止中）の再開予定／再開候補（S7）
    live_sids = set(agents.keys())  # プロセスが生きているものは休眠から除外（幽霊含め再開提案しない）
    # 案件ごとの「稼働中の最大S番号」＝番号付き候補の旧番号除外に使う（S6稼働中ならS4/S5は候補外）
    live_proj_max = {}
    for r in rows:
        if r.get("session_no") is not None:
            p = r["project"]
            live_proj_max[p] = max(live_proj_max.get(p, -1), r["session_no"])
    pinned_rows, suggest_unnum, suggest_num = _dormant_rows(
        cfg, claude_dir, live_sids, live_proj_max, pins, mapper, ws_labels, now_epoch)

    # 委託ジョブ（agent_jobs 台帳）
    jobs_running, jobs_finished = _load_jobs(
        claude_dir, now_epoch, cfg.get("job_recent_done_hours", 24))
    return (rows, skipped, pinned_rows, suggest_unnum, suggest_num,
            jobs_running, jobs_finished, degraded)


def render(rows, skipped=0, pinned_rows=None, suggest_unnum=None, suggest_num=None,
           jobs_running=None, jobs_finished=None, degraded=False):
    now = datetime.now().strftime("%H:%M:%S")
    n_wait = sum(1 for r in rows if r["state"].startswith("waiting"))
    n_jobs = len(jobs_running or [])
    jobs_note = f"・委託 {n_jobs}" if n_jobs else ""
    print(f"🖥️ エージェントライブボード {now} — 稼働 {len(rows)} セッション"
          f"（うち待ち {n_wait}）{jobs_note}\n")
    if degraded:
        print("⚠️ 縮退表示: claude agents --json 不可のため hook 状態(24h)の近似"
              "＝稼働/休眠の区別が不正確。watcher 再起動か claude CLI を確認\n")
    if not rows:
        print("（稼働中セッションなし）")
    else:
        print(_pad("状態", 18) + _pad("案件", 22) + _pad("S", 5) + _pad("更新", 10)
              + _pad("WS", 16) + _pad("model", 13) + _pad("pid", 7) + "タイトル")
        print("-" * 128)
        for r in rows:
            state = STATE_LABELS.get(r["state"], "⚪ 不明")
            if r.get("wait_sec") is not None:      # 待ち時間を状態に併記
                state += f" {_dur(r['wait_sec'])}"
            if r.get("is_new"):                    # 新着（待ち入りたて＝未確認の可能性大）
                state = "🆕" + state
            project = ("📌" + r["project"]) if r.get("pinned") else r["project"]
            print(_pad(state, 18) + _pad(project, 22) + _pad(r["s_no"], 5)
                  + _pad(r["updated"], 10) + _pad(r["ws"], 16)
                  + _pad(r.get("model") or "", 13) + _pad(str(r["pid"]), 7)
                  + _trim(r["title"], 50))
    if skipped:
        print(f"\n※ 非表示: 会話実体のない空セッション {skipped}件"
              "（Cursor復元時の重複起動プロセス）")
    _render_jobs(jobs_running or [], jobs_finished or [])
    _render_dormant(pinned_rows or [], suggest_unnum or [], suggest_num or [])


def _render_jobs(running, finished):
    """委託ジョブ（⏳running・直近の完了/失敗）をターミナルに表示。無ければ何も出さない。"""
    if not running and not finished:
        return
    print("\n⏳ 委託中（ローカルLLMバッチ等・agent_jobs 台帳）")
    for r in running:
        mark = ("⚠️応答なし " + _dur(r["upd_sec"])) if r["stale"] else "⏳ 実行中"
        target = r["project"] + (f" → {r['agent']}" if r["agent"] else "")
        note = f"  {r['note']}" if r["note"] else ""
        print("  " + _pad(mark, 16) + _pad(target, 34) + _pad(r["prog"], 8)
              + _pad("経過 " + r["elapsed"], 12) + _pad("更新 " + r["upd_age"], 13)
              + _trim(r["title"] + note, 44))
    for r in finished:
        icon = "✅" if r["state"] == "done" else "❌"
        summ = f" — {r['summary']}" if r["summary"] else ""
        print(f"  {icon} {r['upd_age']}  {r['project']}  {_trim(r['title'] + summ, 64)}")


def _render_dormant(pinned_rows, suggest_unnum, suggest_num):
    """休眠セッション（プロセス停止中）の再開予定／再開候補をターミナルに表示。"""
    def _line(r, with_note):
        note = f"  — {r['note']}" if (with_note and r["note"]) else ""
        return ("  " + _pad(r["sid_short"], 9) + _pad(r["project"], 22) + _pad(r["s_no"], 5)
                + _pad("最終 " + r["last_age"], 13) + _trim(r["title"], 40) + note)
    if pinned_rows:
        print("\n📌 再開予定（ピン済み・プロセス停止中）")
        for r in pinned_rows:
            print(_line(r, True))
    if suggest_num:
        print("\n💤 再開候補・番号付き（案件の最新Sのみ・未ピン）  ← ピン: pin <sid>")
        for r in suggest_num:
            print(_line(r, False))
    if suggest_unnum:
        print("\n💤 再開候補・番号なし単独（長め遡り・未ピン）"
              "  ← もっと遡る: candidates [--days N]")
        for r in suggest_unnum:
            print(_line(r, False))


# 状態色は固定パレット（good/warning/critical）。テーマで振り替えない＝ダーク/ライトで同じ意味。
# 「色だけに意味を載せない」ため必ずアイコン＋ラベルとセットで出す（ライト面の🟡は3:1未満）。
STATE_COLORS = {
    "working": ("#0ca30c", "作業中", "🟢"),
    "waiting_input": ("#fab219", "待機中", "🟡"),
    "waiting_permission": ("#d03b3b", "許可待ち", "🔴"),
}
LONG_WAIT_SEC = 600  # これ以上待たせていたら待ち時間を強調（放置の気づき）

# 静的ダッシュボード（claude_dashboard_gen.py）と同じトークン一式。テーマ切替は変数の差し替えだけ。
# localStorage のキーも共通（csv-theme）＝2画面で見た目が揃う。
LIVE_CSS = """
*{box-sizing:border-box}
:root{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10); --wash:rgba(255,255,255,.055);
  --newrow:rgba(255,255,255,.05); --cat-1:#3987e5;
}
:root[data-theme="light"]{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10); --wash:rgba(11,11,11,.045);
  --newrow:rgba(11,11,11,.035); --cat-1:#2a78d6;
}
body{margin:0;padding:22px 20px 44px;background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Yu Gothic UI",Meiryo,sans-serif;
  font-size:13px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px}
h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.01em}
.sub{color:var(--muted);font-size:11.5px;margin-top:3px}
.tgl{background:none;border:1px solid var(--border);color:var(--ink2);border-radius:999px;
  padding:5px 13px;font-size:11.5px;cursor:pointer;font-family:inherit;white-space:nowrap;
  text-decoration:none;display:inline-block}
.tgl:hover{background:var(--wash);color:var(--ink)}
.tgl.cur{background:var(--wash);color:var(--ink);cursor:default}
/* 次の更新までの鼓動（アニメ長＝リフレッシュ間隔。止まって見えたら watcher が死んでいる） */
.pulse{height:2px;background:var(--grid);border-radius:2px;overflow:hidden;margin:13px 0 14px}
.pulse i{display:block;height:2px;width:0;background:var(--cat-1);
  animation:fill linear forwards;opacity:.85}
@keyframes fill{from{width:0}to{width:100%}}
.tiles{display:grid;grid-template-columns:repeat(5,minmax(110px,1fr));gap:10px;margin-bottom:18px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:11px;padding:12px 15px}
.tile .l{color:var(--muted);font-size:11px}
.tile .v{font-size:30px;font-weight:600;line-height:1.15;margin-top:1px;color:var(--ink)}
.tile.zzz .v{color:var(--ink2)}
h2{font-size:12.5px;font-weight:600;margin:20px 0 7px;letter-spacing:.02em}
h2 .s{font-weight:400;font-size:11px;color:var(--muted);margin-left:6px}
h2 code{background:var(--wash);border-radius:4px;padding:1px 6px;font-size:10.5px}
table{border-collapse:separate;border-spacing:0 3px;width:100%;font-size:12.5px}
td{background:var(--surface);padding:9px 12px;vertical-align:middle}
td:first-child{border-radius:9px 0 0 9px;border-left:3px solid var(--rail,transparent);
  white-space:nowrap}
td:last-child{border-radius:0 9px 9px 0}
tr.new td{background:var(--newrow)}
/* 状態は「アイコン＋ラベル＋左レール」の3重表現。色だけに意味を載せない */
.badge{color:var(--ink2);white-space:nowrap}
.wait{color:var(--muted);font-size:11px;margin-left:5px}
.wait.long{color:var(--ink);font-weight:600}
.nb{margin-right:4px}
.pj{font-weight:600;white-space:nowrap}
/* 案件名→エージェントカウンターの台帳ページ（生成済みのときだけリンクになる） */
.pj a{color:inherit;text-decoration:none;border-bottom:1px dotted var(--border)}
.pj a:hover{border-bottom-color:var(--cat-1);color:var(--cat-1)}
.sn{color:var(--muted);font-variant-numeric:tabular-nums}
.up,.ws{color:var(--ink2);white-space:nowrap}
.title{color:var(--muted);width:99%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  max-width:0}
.empty{background:var(--surface);border:1px solid var(--border);border-radius:11px;
  padding:14px 16px;color:var(--muted);font-size:12px}
.note{color:var(--muted);font-size:11px;margin-top:10px}
/* 縮退表示の警告帯: agents --json 不可＝稼働/休眠が近似になっている時だけ出す */
.degrade{background:rgba(208,59,59,.12);border:1px solid rgba(208,59,59,.45);
  border-radius:9px;padding:9px 14px;margin-bottom:14px;font-size:12px;color:var(--ink)}
/* 新規WTタブのボタン列: 新規は「どのWSで焚くか」が必須入力＝WS別ボタン（claude-wt://new/） */
.newbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:14px;font-size:12px}
.newbar .s{color:var(--muted);font-size:11px;margin-right:2px}
.newbar a{display:inline-block;border:1px solid var(--border);border-radius:6px;
  padding:2px 10px;color:var(--ink2);text-decoration:none}
.newbar a:hover{background:var(--wash);color:var(--ink)}
table.dormant{font-size:12px}
table.dormant td{padding:7px 12px}
.sid{font-family:ui-monospace,Consolas,monospace;color:var(--muted);font-size:11px}
.dnote{color:var(--ink2)}
/* 休眠行の再開アクション: ▶=claude-wt://（レジストリ登録済み環境のみ動く）／📋=resumeコマンドをコピー */
.act{white-space:nowrap;text-align:right}
.act a,.act .cp{display:inline-block;border:1px solid var(--border);border-radius:6px;
  padding:1px 8px;font-size:11px;color:var(--ink2);background:none;cursor:pointer;
  text-decoration:none;font-family:inherit;line-height:1.7}
.act a:hover,.act .cp:hover{background:var(--wash);color:var(--ink)}
/* 委託ジョブ: 進捗ミニバーとエンジン名。色はセッション状態と同じ固定パレット */
.jbar{height:4px;background:var(--grid);border-radius:2px;overflow:hidden;max-width:130px;
  margin-top:4px}
.jbar i{display:block;height:100%;background:var(--cat-1)}
.jagent{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted)}
footer{margin-top:24px;color:var(--muted);font-size:11px;line-height:1.8}
@media (max-width:760px){.tiles{grid-template-columns:repeat(2,1fr)}.ws{display:none}}
""" + MDL_CSS

LIVE_JS = """
(function(){
  var root=document.documentElement, btn=document.getElementById('tgl');
  function lab(){btn.textContent = root.dataset.theme==='light' ? '☾ ダーク' : '☀ ライト';}
  lab();
  btn.onclick=function(){
    root.dataset.theme = root.dataset.theme==='light' ? 'dark' : 'light';
    try{localStorage.setItem('csv-theme', root.dataset.theme);}catch(e){}
    lab();
  };
  // 📋: resume コマンドをコピー（file:// も secure context 扱いで clipboard API が使える）
  document.addEventListener('click', function(e){
    var b = e.target.closest ? e.target.closest('.cp') : null;
    if(!b) return;
    navigator.clipboard.writeText('claude --resume ' + b.dataset.sid).then(function(){
      b.textContent = '✓';  // meta refresh で数秒後に元へ戻るので戻し処理は最小限
      setTimeout(function(){ b.textContent = '📋'; }, 1500);
    });
  });
  // ▶/➕（claude-wt://）は hidden iframe で発火する。<a> の素ナビゲーションだと
  // 「新しい遷移の開始」で予約済み meta refresh がキャンセルされ、外部プロトコル
  // ゆえ遷移自体は中断→再スケジュールもされず、そのタブは自動更新が止まる
  // （S16 ふくちゃん報告の「ボタンを押すと更新が止まる」の真因）。
  // iframe のナビゲーションは子フレーム扱い＝top の更新予約に触らない。
  var wtfr = document.createElement('iframe');
  wtfr.style.display = 'none';
  document.body.appendChild(wtfr);
  document.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[href^="claude-wt:"]') : null;
    if(!a) return;
    e.preventDefault();
    wtfr.src = a.href;
  });
  // 保険のウォッチドッグ: meta refresh が何かの理由で死んでいたら JS 側でリロード。
  // 正常時はこのタイマーが発火する前にページごと入れ替わるので何もしない。
  var mr = document.querySelector('meta[http-equiv="refresh"]');
  var sec = mr ? parseInt(mr.content, 10) : 10;
  if(sec > 0) setTimeout(function(){ location.reload(); }, (sec + 2) * 1000);
})();
"""
# 自動リロード（meta refresh）でテーマが一瞬戻るのを防ぐため、描画前に data-theme を当てる
LIVE_HEAD_JS = "try{var t=localStorage.getItem('csv-theme');" \
               "if(t)document.documentElement.dataset.theme=t;}catch(e){}"


def _pj_cell(project, pin=""):
    """案件名セル。トラック個別ページ（track_record_gen.py 生成）があればそこへリンクする。

    存在チェックはファイル名の有無だけ＝台帳の中身は読まない（watch ループで毎秒回るため）。
    slug() は track_record_gen.py と共有＝案件名が一致すれば同じファイル名を指す。
    ページが無い案件はただの文字列に戻るので、個別ページ未生成でも板は壊れない。
    """
    esc = html_mod.escape
    name = f"{slug(project)}.html" if project else ""
    if name and (REPO_ROOT / "reports" / "track" / name).is_file():
        return f'<td class="pj">{pin}<a href="track/{name}">{esc(project)}</a></td>'
    return f'<td class="pj">{pin}{esc(project)}</td>'


def _live_rows_html(rows):
    esc = html_mod.escape
    out = []
    for r in rows:
        color, label, icon = STATE_COLORS.get(r["state"], ("#898781", "不明", "⚪"))
        wait = ""
        if r.get("wait_sec") is not None:  # 待ち時間を併記し、長引いたら強調（放置の気づき）
            cls = "wait long" if r["wait_sec"] >= LONG_WAIT_SEC else "wait"
            wait = f'<span class="{cls}">{_dur(r["wait_sec"])}</span>'
        newbadge = '<span class="nb">🆕</span>' if r.get("is_new") else ''  # 新着＝未確認の目印
        pin = '📌' if r.get("pinned") else ''  # 稼働中のピン済み
        out.append(
            f'<tr class="{"new" if r.get("is_new") else ""}" style="--rail:{color}">'
            f'<td>{newbadge}<span class="badge">{icon} {label}</span>{wait}</td>'
            f'{_pj_cell(r["project"], pin)}'
            f'<td class="sn">{esc(r["s_no"])}</td>'
            f'<td class="up">{esc(r["updated"])}</td><td class="ws">{esc(r["ws"])}</td>'
            f'<td>{model_badge(r.get("model"))}</td>'
            f'<td class="title">{esc(_trim(r["title"], 90))}</td></tr>')
    return "".join(out)


def _newtab_html():
    """「➕ 新規WTタブ」ボタン列（claude-wt://new/<WS名>）を claude-wt/workspaces.json から作る。

    resume と違い、新規セッションは「どのワークスペースで焚くか」が必須入力
    （cwd がセッションの所属先＝採番・メモリ・地図の入り口を決める）ので WS別ボタンにする。
    選択肢とハンドラ側の検証ホワイトリストは同じ workspaces.json＝定義の一本化。
    ファイルが無い/壊れている環境ではボタン列ごと消えるだけで板は壊れない。
    """
    esc = html_mod.escape
    path = REPO_ROOT / "claude-wt" / "workspaces.json"
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f).get("workspaces") or []
    except (OSError, json.JSONDecodeError):
        return ""
    btns = "".join(
        f'<a href="claude-wt://new/{quote(e["name"])}" title="{esc(e.get("path", ""))}">'
        f'➕ {esc(e["name"])}</a>'
        for e in entries if e.get("name"))
    if not btns:
        return ""
    return f'<div class="newbar"><span class="s">新規WTタブ</span>{btns}</div>'


def _dormant_html(pinned_rows, suggest_unnum, suggest_num):
    """休眠セッションの再開予定／再開候補を HTML セクションとして返す。"""
    esc = html_mod.escape

    def _rows_html(rows, with_note):
        out = []
        for r in rows:
            note = (f'<span class="dnote">— {esc(r["note"])}</span>'
                    if (with_note and r["note"]) else '')
            out.append(
                f'<tr><td class="sid">{esc(r["sid_short"])}</td>'
                f'{_pj_cell(r["project"])}<td class="sn">{esc(r["s_no"])}</td>'
                f'<td class="up">最終 {esc(r["last_age"])}</td>'
                f'<td class="title">{esc(_trim(r["title"], 70))} {note}</td>'
                f'<td class="act"><a href="claude-wt://resume/{esc(r["sid"])}"'
                f' title="Windows Terminal で再開（claude-wt:// 登録が必要）">▶ 開く</a> '
                f'<button class="cp" type="button" data-sid="{esc(r["sid"])}"'
                f' title="claude --resume コマンドをコピー">📋</button></td></tr>')
        return "".join(out)

    sections = []
    if pinned_rows:
        sections.append(
            '<h2>📌 再開予定<span class="s">ピン済み・プロセス停止中</span></h2>'
            f'<table class="dormant">{_rows_html(pinned_rows, True)}</table>')
    if suggest_num:
        sections.append(
            '<h2>💤 再開候補・番号付き<span class="s">案件の最新Sのみ・未ピン　'
            'ピン <code>live_board.py pin &lt;sid&gt;</code></span></h2>'
            f'<table class="dormant">{_rows_html(suggest_num, False)}</table>')
    if suggest_unnum:
        sections.append(
            '<h2>💤 再開候補・番号なし単独<span class="s">長め遡り・未ピン　'
            'もっと遡る <code>live_board.py candidates --days N</code></span></h2>'
            f'<table class="dormant">{_rows_html(suggest_unnum, False)}</table>')
    return "".join(sections)


JOB_COLORS = {"running": "#3987e5", "stale": "#d03b3b", "done": "#0ca30c", "failed": "#d03b3b"}


def _jobs_html(running, finished):
    """委託ジョブの HTML テーブル（running → 直近の done/failed の順）。"""
    esc = html_mod.escape
    if not running and not finished:
        return ('<div class="empty">なし — 委託ジョブは動いていない'
                '（配線: agent-graph/agent_job.py）</div>')
    out = []
    for r in running:
        color = JOB_COLORS["stale" if r["stale"] else "running"]
        if r["stale"]:  # 最終更新が stale_sec 途絶＝死んだ疑い。ボードを見る動機の本丸
            badge = ('<span class="badge">⚠️ 応答なし</span>'
                     f'<span class="wait long">{_dur(r["upd_sec"])}</span>')
        else:
            badge = ('<span class="badge">⏳ 実行中</span>'
                     f'<span class="wait">経過 {esc(r["elapsed"])}</span>')
        bar = (f'<div class="jbar"><i style="width:{r["pct"]}%"></i></div>'
               if r["pct"] is not None else "")
        note = f' — {esc(r["note"])}' if r["note"] else ""
        out.append(
            f'<tr style="--rail:{color}">'
            f'<td>{badge}</td>'
            f'<td class="pj">{esc(r["project"])}</td>'
            f'<td class="sn">{esc(r["prog"])}{bar}</td>'
            f'<td class="up">更新 {esc(r["upd_age"])}</td>'
            f'<td class="ws"><span class="jagent">{esc(r["agent"])}</span></td>'
            f'<td class="title">{esc(_trim(r["title"], 60))}{note}</td></tr>')
    for r in finished:
        ok = r["state"] == "done"
        summ = f' — {esc(r["summary"])}' if r["summary"] else ""
        out.append(
            f'<tr style="--rail:{JOB_COLORS["done" if ok else "failed"]}">'
            f'<td><span class="badge">{"✅ 完了" if ok else "❌ 失敗"}</span></td>'
            f'<td class="pj">{esc(r["project"])}</td>'
            f'<td class="sn">{esc(r["prog"])}</td>'
            f'<td class="up">{esc(r["upd_age"])}</td>'
            f'<td class="ws"><span class="jagent">{esc(r["agent"])}</span></td>'
            f'<td class="title">{esc(_trim(r["title"], 60))}{summ}</td></tr>')
    return f'<table>{"".join(out)}</table>'


def render_html(rows, refresh_sec, skipped=0, pinned_rows=None,
                suggest_unnum=None, suggest_num=None,
                jobs_running=None, jobs_finished=None, degraded=False, messages_html=""):
    now = datetime.now().strftime("%H:%M:%S")
    pinned_rows, suggest_unnum = pinned_rows or [], suggest_unnum or []
    suggest_num = suggest_num or []
    jobs_running, jobs_finished = jobs_running or [], jobs_finished or []
    n_stale = sum(1 for r in jobs_running if r["stale"])
    # セクション順は ⚙️動いてる → ⏳委託中 → 👋あなた待ち（2026-08-02 ユーザー要望で入替。
    # 「要対応の即答」は上部の状態タイルが担うので、警報機能はタイル側で維持される整理）
    need_me = [r for r in rows if r["state"].startswith("waiting")]
    running = [r for r in rows if not r["state"].startswith("waiting")]
    n_perm = sum(1 for r in need_me if r["state"] == "waiting_permission")
    n_input = len(need_me) - n_perm

    def tile(label, n, cls=""):
        return (f'<div class="tile {cls}"><div class="l">{label}</div>'
                f'<div class="v">{n}</div></div>')

    tiles = (tile("🔴 許可待ち", n_perm) + tile("🟡 待機中", n_input)
             + tile("⚠️ 委託応答なし" if n_stale else "⏳ 委託中",
                    n_stale if n_stale else len(jobs_running),
                    "" if jobs_running else "zzz")
             + tile("🟢 作業中", len(running))
             + tile("💤 休眠（ピン＋候補）",
                    len(pinned_rows) + len(suggest_num) + len(suggest_unnum), "zzz"))

    if need_me:
        need_html = f'<table>{_live_rows_html(need_me)}</table>'
    else:
        need_html = '<div class="empty">なし — いま手を止めて待っているセッションはない</div>'
    run_html = (f'<table>{_live_rows_html(running)}</table>' if running else
                '<div class="empty">なし — 動いているセッションはない</div>')
    footnote = (f'<div class="note">※ 非表示: 会話実体のない空セッション {skipped}件'
                "（Cursor復元時の重複起動プロセス）</div>") if skipped else ""
    degrade_html = ('<div class="degrade">⚠️ 縮退表示: <code>claude agents --json</code> が'
                    '実行できないため hook 状態(24h)の近似で表示中＝稼働/休眠の区別が不正確。'
                    'watcher の再起動（live_board.bat）か claude CLI の状態を確認</div>'
                    if degraded else "")
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_sec}">
<title>{"⚠️" if n_stale else ""}エージェントライブボード — 待ち {len(need_me)} / 委託 {len(jobs_running)} / 稼働 {len(rows)}</title>
<script>{LIVE_HEAD_JS}</script>
<style>{LIVE_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>🖥️ エージェントライブボード</h1>
    <div class="sub">生成 {now} ／ {refresh_sec}秒ごと自動更新（裏で <code>live_board.py --html --watch</code>）</div>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    {nav_html("board")}
    <button class="tgl" id="tgl" type="button">☀ ライト</button>
  </div>
</header>
<div class="pulse"><i style="animation-duration:{refresh_sec}s"></i></div>
{degrade_html}
{_newtab_html()}
<div class="tiles">{tiles}</div>

<h2>⚙️ 動いてる<span class="s">更新の新しい順</span></h2>
{run_html}

<h2>⏳ 委託中<span class="s">ローカルLLMバッチ等（agent_jobs 台帳）・応答なしは ⚠️</span></h2>
{_jobs_html(jobs_running, jobs_finished)}

<h2>👋 あなた待ち<span class="s">許可待ち → 待機中の順・新しいものが上</span></h2>
{need_html}
{footnote}

<h2>📨 最近のやり取り<span class="s">セッション間メッセージ（SendMessage）・新しい順・配置図は 🏢 オフィス</span></h2>
{messages_html}
{_dormant_html(pinned_rows, suggest_unnum, suggest_num)}
<footer>
  ピン <code>python live_board.py pin &lt;sid&gt; [メモ]</code>
  ／ 全体の地図は <code>claude_dashboard.bat</code>
  ／ ▶ 開く は初回のみ <code>claude-wt\\register_protocol.ps1</code> の実行が必要
</footer>
</div>
<script>{LIVE_JS}</script>
</body>
</html>"""


def _write_tabs_snapshot(rows):
    """稼働セッションの構成を tabs_snapshot.json に保存（claude-wt\\restore_tabs.ps1 の復元元）。

    「前回のタブ状態」の正体＝watcher が最後に見た稼働セッション一覧。空のときは書かない＝
    全タブを閉じた後・PC再起動後も「最後に何か動いていた状態」が残り、翌朝それを復元できる。
    reports/ 配下（gitignore）＝タイトルに機密が入り得るのでリポには積まない。
    """
    if not rows:
        return
    sessions = [{"sid": r["sid"], "cwd": r["cwd"], "project": r["project"],
                 "s_no": r["s_no"], "title": _trim(r["title"], 60),
                 "model": r.get("model") or ""}
                for r in rows if r.get("cwd")]
    if not sessions:
        return
    out = {"saved_at": datetime.now().isoformat(timespec="seconds"), "sessions": sessions}
    path = REPO_ROOT / "reports" / "tabs_snapshot.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


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


def _cmd_candidates(cfg, claude_dir, days):
    """再開候補を『遡って確認』する一覧（ボードの抑制＝最新Sのみ/短lookback を外した全表示）。

    案件マップ済・プロセス停止中・未ピンのセッションを days 日遡って更新順に全部出す。
    番号なし単独が主眼だが、番号付きの旧番号もここでは隠さず出す（ボードは抑制・ここは全部見せ）。
    """
    mapper = ProjectMapper(cfg)
    ws_labels = cfg.get("workspace_labels", {})
    pins = _load_pins(cfg)
    pin_sids = {p["sid"] for p in pins}
    agents = _agents_json() or {}
    live_sids = set(agents.keys())
    now_epoch = datetime.now(timezone.utc).timestamp()
    cutoff = now_epoch - days * 86400

    rows = []
    for s, jf, ws in _iter_session_files(claude_dir):
        if s in live_sids or s in pin_sids:
            continue
        mtime = jf.stat().st_mtime
        if mtime < cutoff:
            continue
        summary = _peek(jf, ws)
        mapper.map_session(summary)
        if not summary.get("project") or not summary.get("first_user_text"):
            continue
        rows.append(_dormant_row(s, summary, mtime, ws, ws_labels, ""))
    rows.sort(key=lambda r: (1 if r["s_no"] else 0, -r["last_epoch"]))

    n_unnum = sum(1 for r in rows if not r["s_no"])
    print(f"💤 再開候補（直近{days}日・案件マップ済・停止中・未ピン）"
          f" {len(rows)}件（番号なし {n_unnum} / 番号付き {len(rows) - n_unnum}）")
    print("  ← ピン留め: python live_board.py pin <sid接頭辞> [メモ]\n")
    if not rows:
        print("（該当なし）")
        return
    print("  " + _pad("sid", 9) + _pad("案件", 22) + _pad("S", 5)
          + _pad("最終更新", 13) + "タイトル")
    for r in rows:
        print("  " + _pad(r["sid_short"], 9) + _pad(r["project"], 22) + _pad(r["s_no"], 5)
              + _pad("最終 " + r["last_age"], 13) + _trim(r["title"], 46))


def _messages(cfg, rows):
    """セッション間メッセージ（S21）: jsonl と稼働台帳から送受信を突合し、ボード用 HTML と
    オフィス図用の素材（edges・labels）を返す。失敗してもボードは止めない（空で返す）。"""
    try:
        claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
        hours = (cfg.get("office") or {}).get("window_hours", 48)
        res = collect_messages(claude_dir, REPO_ROOT / "cache" / "messages_cache.json", hours)
        known = {r["sid"]: {"project": r.get("project"), "s": r.get("session_no"),
                            "ws": r.get("ws", ""), "ws_key": r.get("ws_key", ""),
                            "cwd": r.get("cwd", ""), "title": r.get("title", "")}
                 for r in rows if r.get("sid")}
        labels = label_sessions(res["sids"], claude_dir, ProjectMapper(cfg),
                                cfg.get("workspace_labels"), known)
        return recent_html(res["edges"], labels, cfg, limit=12, esc=html_mod.escape), res, labels
    except Exception as e:  # noqa: BLE001
        print(f"messages error: {e!r}")
        return '<div class="empty">取得エラー（messages.py）</div>', None, {}


def main():
    args = sys.argv[1:]

    # 再開候補を遡って一覧（番号なし含む全表示・ボードの抑制を外す）
    if args and args[0] == "candidates":
        cfg = load_config(REPO_ROOT)
        claude_dir = Path(cfg.get("claude_dir", "~/.claude")).expanduser()
        days = cfg.get("dormant_lookback_days_unnumbered", 30)
        if "--days" in args:
            i = args.index("--days")
            if len(args) > i + 1 and args[i + 1].isdigit():
                days = int(args[i + 1])
        _cmd_candidates(cfg, claude_dir, days)
        return

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
        (rows, skipped, pinned_rows, suggest_unnum, suggest_num,
         jobs_running, jobs_finished, degraded) = build_rows()
        if not degraded:
            # 縮退中は書かない＝閉じたセッション混じりの近似データで「前回状態」を汚染しない
            _write_tabs_snapshot(rows)  # タブ復元用（claude-wt/restore_tabs.ps1 が読む）
        if html_mode:
            out_path.parent.mkdir(exist_ok=True)
            cfg_now = load_config(REPO_ROOT)
            msg_html, msg_res, msg_labels = _messages(cfg_now, rows)
            out_path.write_text(
                render_html(rows, watch or 10, skipped, pinned_rows,
                            suggest_unnum, suggest_num, jobs_running, jobs_finished,
                            degraded=degraded, messages_html=msg_html),
                encoding="utf-8")
            print(f"{datetime.now().strftime('%H:%M:%S')} updated -> {out_path}")
            try:  # バーチャルオフィス配置図（S21 段階②）: 同じループに相乗り
                if msg_res is not None and write_office(cfg_now, out_path.parent, msg_res, msg_labels, rows):
                    print(f"{datetime.now().strftime('%H:%M:%S')} office updated")
            except Exception as e:  # noqa: BLE001
                print(f"office view error: {e!r}")
            try:  # 日誌ビュー（S20）: 同じループに相乗り＝プロセスは1個のまま。失敗してもボードは止めない
                if write_daily(load_config(REPO_ROOT), out_path.parent):
                    print(f"{datetime.now().strftime('%H:%M:%S')} daily updated")
            except Exception as e:  # noqa: BLE001
                print(f"daily view error: {e!r}")
        else:
            if watch:
                os.system("cls" if os.name == "nt" else "clear")
            render(rows, skipped, pinned_rows, suggest_unnum, suggest_num,
                   jobs_running, jobs_finished, degraded=degraded)
        if not watch:
            break
        time.sleep(watch)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        pass
