# -*- coding: utf-8 -*-
"""外注ジョブ台帳（エージェントグラフ Phase 1）— start / progress / done / fail。

Claude セッションとして見えない「外注仕事」（ローカルLLMバッチ等）を
~/.claude/hooks/agent_jobs/<job_id>.json に1ジョブ1ファイルで記録する。
live_board.py がこれを読んで「⏳ 外注中」セクションに出す
（running の進捗・経過・応答なし強調＝ジョブが死んだら気づける）。
グラフの最初のエッジ実データでもある: project(依頼元) → agent(実行体) の依頼が job として残る。

原則（session-visualizer と同じ）: ローカル完結・台帳に本文は載せない（一行サマリのみ）。
JSON の ts 形式は hooks/session_state と同じ UTC "%Y-%m-%dT%H:%M:%SZ"。

CLI（シェル/batから）:
  python agent_job.py start --title "夜間バッチ" --project "サンプル案件" \
         --agent "ollama:qwen2.5:14b" [--total 25] [--job-id nightly-batch] [--stale-sec 1800]
      → job_id を stdout に1行出す（呼び出し側が保存して以降のコマンドに渡す）
  python agent_job.py progress <job_id> [--cur 3] [--total 25] [--note "2026-07-13 処理中"]
  python agent_job.py done <job_id> [--summary "25日分完了"]
  python agent_job.py fail <job_id> [--summary "ollama接続不可"]
  python agent_job.py list
  python agent_job.py history [--days 7]   # 完了/失敗の実績（history.jsonl）を表示

Python（バッチに import して使う。バッチを絶対に壊さないよう呼ぶ側は try/except で包む）:
  import agent_job
  jid = agent_job.start(title="...", project="...", agent="...", total=25, job_id="nightly-batch")
  agent_job.progress(jid, cur=i, note=date)   # note は状況の一行。呼ぶこと自体が heartbeat
  agent_job.done(jid, summary="...")  /  agent_job.fail(jid, summary="...")

設計メモ:
- job_id を明示指定すると同名ファイルを上書き＝「バッチ種別ごとに1枠」の運用
  （再起動で古い記録が置き換わる）。
- progress はファイルが消えていても骨格を再生成する（prune や手動削除でバッチを殺さない）。
- 履歴: done/fail 時に history.jsonl へ1行追記（S2 2026-08-02・ライブボード要望）。
  完了1件＝グラフの確定エッジ1本。<job_id>.json が上書きで消えても実績はここに残る。
  1行= {job_id, result(done/failed), title, project, agent, started, ended, dur_sec,
  cur, total, summary, note}。すでに done/failed のジョブへ再度 done/fail しても追記しない
  （重複エッジ防止）。追記はベストエフォート＝失敗しても状態書き込みとバッチは殺さない。

掃除ポリシー（明文化 S2）:
- <job_id>.json（いまの状態）: start 時に prune。done/failed は7日、それ以外
  （宙ぶらりんの running・壊れたファイル含む）は30日で削除。
  running を短期で消さないのは「死んだジョブこそ ⚠️ で見え続けるべき」だから。
- history.jsonl（実績）: prune しない＝追記専用の正本。読み手は壊れた行を読み飛ばすこと。
  肥大が問題になったら年次ローテ（history-2026.jsonl 等）を Phase 3 で検討。
"""
import argparse
import calendar
import json
import os
import re
import sys
import time
from pathlib import Path

STALE_SEC_DEFAULT = 1800   # 最終更新からこの秒数途絶えたら live_board が ⚠️応答なし 表示
PRUNE_DONE_DAYS = 7
PRUNE_ANY_DAYS = 30


def _jobs_dir() -> Path:
    """台帳ディレクトリ。読み手の live_board は config の claude_dir（既定 ~/.claude）から
    同じ場所を解決する。claude_dir を既定から変えるならここも合わせること（現状 home 固定）。"""
    d = Path.home() / ".claude" / "hooks" / "agent_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sanitize(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", job_id or "").strip("-.") or "job"


def _path(job_id: str) -> Path:
    return _jobs_dir() / (_sanitize(job_id) + ".json")


def _write(obj: dict):
    """アトミック書き込み（live_board が半端な JSON を読まないように tmp→replace）。

    Windows の os.replace は読み手（live_board watcher が10秒毎に read）と重なると
    PermissionError になり得る（CPython の open は FILE_SHARE_DELETE なし）。1回きりの
    done/fail がこれで落ちると running が残置され誤⚠️が続くので、短リトライで閉じる。
    """
    path = _path(obj["job_id"])
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    last = None
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.1)
    try:
        tmp.unlink()
    except OSError:
        pass
    raise last


def _history_path() -> Path:
    return _jobs_dir() / "history.jsonl"


def _history_append(j: dict):
    """確定エッジ1本を history.jsonl へ1行追記（追記専用・ベストエフォート）。

    ここが失敗しても done/fail の状態書き込みは既に済んでいる＝バッチにも板にも影響なし。
    dur_sec は板が平均所要を出す用の事前計算（parse できなければ None）。
    """
    try:
        rec = {"job_id": j.get("job_id"), "result": j.get("state")}
        for k in ("title", "project", "agent", "started", "ended"):
            rec[k] = j.get(k)
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            rec["dur_sec"] = calendar.timegm(time.strptime(j["ended"], fmt)) \
                - calendar.timegm(time.strptime(j["started"], fmt))
        except Exception:
            rec["dur_sec"] = None
        for k in ("cur", "total", "summary", "note"):
            rec[k] = j.get(k)
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(_history_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _read(job_id: str):
    try:
        with open(_path(job_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _prune():
    """古い記録の掃除（ベストエフォート。何があっても例外を外に出さない）。

    done/failed は7日、それ以外（読めない・dict でない等の壊れたファイル含む）は30日で削除。
    running を短期で消さないのは「死んだジョブこそ ⚠️ で見え続けるべき」だから。
    """
    now = time.time()
    d = _jobs_dir()
    for p in d.glob("*.json"):
        try:
            age = now - p.stat().st_mtime
            state = None
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                state = j.get("state") if isinstance(j, dict) else None
            except (OSError, json.JSONDecodeError):
                pass
            if (state in ("done", "failed") and age > PRUNE_DONE_DAYS * 86400) \
                    or age > PRUNE_ANY_DAYS * 86400:
                p.unlink()
        except Exception:
            continue
    for p in d.glob("*.tmp"):  # replace 失敗で残った書きかけの回収（読み手は拾わないがゴミ掃除）
        try:
            if now - p.stat().st_mtime > 86400:
                p.unlink()
        except Exception:
            continue


# ─── ライブラリ API ─────────────────────────────────────────────────────

def start(title, project="", agent="", total=None, job_id=None,
          stale_sec=STALE_SEC_DEFAULT, note="") -> str:
    """ジョブ開始を記録して job_id を返す。job_id 省略時は時刻から自動採番。"""
    _prune()
    jid = _sanitize(job_id or ("job-" + time.strftime("%Y%m%d-%H%M%S")))
    ts = _now_ts()
    _write({
        "job_id": jid,
        "state": "running",
        "title": title or jid,
        "project": project,        # 依頼元（グラフの from ノード）
        "agent": agent,            # 実行体（グラフの to ノード）例: "ollama:qwen2.5:14b"
        "pid": os.getpid(),
        "started": ts,
        "ts": ts,                  # 最終更新＝heartbeat。progress のたびに更新
        "cur": 0,
        "total": total,
        "note": note,              # いまの状況の一行（本文は載せない）
        "summary": "",
        "stale_sec": int(stale_sec),
    })
    return jid


def progress(job_id, cur=None, total=None, note=None):
    """進捗更新。呼ぶこと自体が heartbeat（ts 更新）。消されていても骨格を再生成。"""
    j = _read(job_id) or {
        "job_id": _sanitize(job_id), "state": "running", "title": _sanitize(job_id),
        "project": "", "agent": "", "pid": os.getpid(),
        "started": _now_ts(), "cur": 0, "total": None, "note": "",
        "summary": "", "stale_sec": STALE_SEC_DEFAULT,
    }
    if cur is not None:
        j["cur"] = cur
    if total is not None:
        j["total"] = total
    if note is not None:
        j["note"] = note
    j["ts"] = _now_ts()
    _write(j)


def _finish(job_id, state, summary):
    j = _read(job_id)
    # すでに確定済みのジョブへの再 done/fail は履歴に積まない（重複エッジ防止）。
    # 状態ファイル自体は更新する（summary の上書き訂正は許す）。
    already_final = j is not None and j.get("state") in ("done", "failed")
    if j is None:
        j = {"job_id": _sanitize(job_id), "title": _sanitize(job_id), "project": "",
             "agent": "", "pid": os.getpid(), "started": _now_ts(),
             "cur": 0, "total": None, "note": "", "stale_sec": STALE_SEC_DEFAULT}
    j["state"] = state
    j["summary"] = summary or ""
    j["ts"] = _now_ts()
    j["ended"] = j["ts"]
    _write(j)
    if not already_final:
        _history_append(j)


def done(job_id, summary=""):
    _finish(job_id, "done", summary)


def fail(job_id, summary=""):
    _finish(job_id, "failed", summary)


# ─── CLI ────────────────────────────────────────────────────────────────

def _cmd_list():
    rows = []
    for p in sorted(_jobs_dir().glob("*.json")):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    if not rows:
        print("（ジョブなし）")
        return
    for j in rows:
        prog = ""
        if j.get("total"):
            prog = f" {j.get('cur', 0)}/{j['total']}"
        note = f" — {j['note']}" if j.get("note") else ""
        summ = f" — {j['summary']}" if j.get("summary") else ""
        print(f"{j.get('state', '?'):8} {j.get('job_id')}{prog}"
              f"  [{j.get('project', '')}→{j.get('agent', '')}] {j.get('title', '')}"
              f"{note}{summ}  (ts {j.get('ts', '?')})")


def _cmd_history(days):
    path = _history_path()
    if not path.exists():
        print("（履歴なし）")
        return
    cutoff = time.time() - days * 86400
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    shown = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                ended_ep = calendar.timegm(time.strptime(r["ended"], fmt))
            except Exception:
                continue   # 壊れた行は読み飛ばす（掃除ポリシーどおり）
            if ended_ep < cutoff:
                continue
            mark = "✅" if r.get("result") == "done" else "❌"
            local = time.strftime("%m-%d %H:%M", time.localtime(ended_ep))
            prog = f" {r.get('cur', '?')}/{r['total']}" if r.get("total") else ""
            dur = ""
            if r.get("dur_sec") is not None:
                m = r["dur_sec"] // 60
                dur = f" {m // 60}h{m % 60:02d}m" if m >= 60 else f" {m}m"
            summ = f" — {r['summary']}" if r.get("summary") else ""
            print(f"{mark} {local} {r.get('job_id')}{prog}{dur}"
                  f"  [{r.get('project', '')}→{r.get('agent', '')}]{summ}")
            shown += 1
    if not shown:
        print(f"（直近{days}日の実績なし）")


def main(argv):
    ap = argparse.ArgumentParser(description="外注ジョブ台帳 (agent-graph Phase 1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="ジョブ開始（job_id を stdout に出す）")
    p.add_argument("--title", required=True)
    p.add_argument("--project", default="")
    p.add_argument("--agent", default="")
    p.add_argument("--total", type=int, default=None)
    p.add_argument("--job-id", default=None)
    p.add_argument("--stale-sec", type=int, default=STALE_SEC_DEFAULT)
    p.add_argument("--note", default="")

    p = sub.add_parser("progress", help="進捗更新（heartbeat 兼用）")
    p.add_argument("job_id")
    p.add_argument("--cur", type=int, default=None)
    p.add_argument("--total", type=int, default=None)
    p.add_argument("--note", default=None)

    for name in ("done", "fail"):
        p = sub.add_parser(name)
        p.add_argument("job_id")
        p.add_argument("--summary", default="")

    sub.add_parser("list", help="ジョブ一覧（デバッグ用）")

    p = sub.add_parser("history", help="完了/失敗の実績表示（history.jsonl）")
    p.add_argument("--days", type=int, default=7)

    a = ap.parse_args(argv)
    if a.cmd == "start":
        print(start(a.title, a.project, a.agent, a.total, a.job_id, a.stale_sec, a.note))
    elif a.cmd == "progress":
        progress(a.job_id, a.cur, a.total, a.note)
    elif a.cmd == "done":
        done(a.job_id, a.summary)
    elif a.cmd == "fail":
        fail(a.job_id, a.summary)
    elif a.cmd == "list":
        _cmd_list()
    elif a.cmd == "history":
        _cmd_history(a.days)


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
