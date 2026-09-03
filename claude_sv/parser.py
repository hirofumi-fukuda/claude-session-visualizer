"""セッション jsonl のストリームパーサー（汎用コア・読み取り専用）。

1ファイル＝1セッション。1行ずつ読み、セッションのサマリー dict を返す。
稼働中セッションの追記途中行（不完全なJSON）は読み飛ばす。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .loc import LocAccumulator

# 環境タグ（IDE通知・システムリマインダー等）はユーザー発話から除去する
_TAG_RE = re.compile(
    r"<(ide_opened_file|ide_selection|ide_diagnostics|system-reminder|"
    r"command-name|command-message|command-args|local-command-stdout|task-notification)"
    r"[^>]*>.*?</\1>",
    re.DOTALL,
)


def _clean_user_text(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _iter_text_items(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                yield item.get("text", "")


def _to_local_dt(ts_iso: str):
    """ISO文字列 → ローカル時刻の datetime（不正なら None）。日別・時間帯別の集計に使う。"""
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def parse_session(path: Path) -> dict:
    """1セッション jsonl をストリームパースしてサマリーを返す。"""
    summary = {
        "session_id": path.stem,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "first_ts": None,
        "last_ts": None,
        "ai_title": None,
        "last_prompt": None,
        "first_user_text": None,
        "user_texts_matchpool": [],  # 採番パターン照合用（先頭数件のみ保持）
        "n_user_prompts": 0,
        "n_assistant": 0,
        "n_tool_use": 0,
        "output_tokens": 0,
        "input_tokens": 0,
        "model": None,  # 最後の assistant の model（/model 途中切替に追従＝live_board._peek_model と同義）
        "cwd": None,
        "version": None,
        "days_active": {},  # "YYYY-MM-DD" -> assistantメッセージ数
        "days_tokens": {},  # "YYYY-MM-DD" -> 出力トークン（日別推移グラフ用）
        "hours_active": {},  # "曜日-時" (0=月〜6=日) -> assistantメッセージ数（稼働リズム用）
        "parse_errors": 0,
        # 書いた行数（loc.py）。loc={added,removed,code,docs,other,bash_added,unmeasured,n_edits}
        # days_loc={"YYYY-MM-DD": {code,docs,other,removed}} files_loc={basename: added}（上位30）
        # サブエージェント（<sid>/subagents/agent-*.jsonl）の編集も同じ集計器に畳む
        "loc": {}, "days_loc": {}, "files_loc": {},
    }
    acc = LocAccumulator()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                summary["parse_errors"] += 1
                continue
            t = rec.get("type")
            ts = rec.get("timestamp")
            if isinstance(ts, str):
                if summary["first_ts"] is None:
                    summary["first_ts"] = ts
                summary["last_ts"] = ts
            if summary["cwd"] is None and rec.get("cwd"):
                summary["cwd"] = rec["cwd"]
            if summary["version"] is None and rec.get("version"):
                summary["version"] = rec["version"]

            if t == "ai-title":
                summary["ai_title"] = rec.get("aiTitle") or summary["ai_title"]
            elif t == "last-prompt":
                summary["last_prompt"] = rec.get("lastPrompt") or summary["last_prompt"]
            elif t == "user":
                msg = rec.get("message") or {}
                _feed_tool_results(acc, msg.get("content"))
                if rec.get("isMeta") or rec.get("isSidechain"):
                    continue
                for raw in _iter_text_items(msg.get("content")):
                    text = _clean_user_text(raw)
                    if not text:
                        continue
                    summary["n_user_prompts"] += 1
                    if summary["first_user_text"] is None:
                        summary["first_user_text"] = text[:200]
                    if len(summary["user_texts_matchpool"]) < 10:
                        summary["user_texts_matchpool"].append(text[:120])
                    break  # 1レコード1発話としてカウント
            elif t == "assistant":
                if rec.get("isSidechain"):
                    continue
                summary["n_assistant"] += 1
                msg = rec.get("message") or {}
                if msg.get("model") and msg["model"] != "<synthetic>":
                    summary["model"] = msg["model"]
                usage = msg.get("usage") or {}
                out_tok = usage.get("output_tokens") or 0
                summary["output_tokens"] += out_tok
                summary["input_tokens"] += usage.get("input_tokens") or 0
                dt = _to_local_dt(ts) if isinstance(ts, str) else None
                if dt:
                    day = dt.strftime("%Y-%m-%d")
                    summary["days_active"][day] = summary["days_active"].get(day, 0) + 1
                    summary["days_tokens"][day] = summary["days_tokens"].get(day, 0) + out_tok
                    slot = f"{dt.weekday()}-{dt.hour}"
                    summary["hours_active"][slot] = summary["hours_active"].get(slot, 0) + 1
                content = msg.get("content")
                if isinstance(content, list):
                    day = dt.strftime("%Y-%m-%d") if dt else None
                    for it in content:
                        if isinstance(it, dict) and it.get("type") == "tool_use":
                            summary["n_tool_use"] += 1
                            acc.on_tool_use(it, day)
    for sub in _subagent_files(path):
        _parse_loc_only(sub, acc)
    summary.update(acc.result())
    return summary


def _feed_tool_results(acc: LocAccumulator, content):
    if isinstance(content, list):
        for it in content:
            if isinstance(it, dict) and it.get("type") == "tool_result":
                acc.on_tool_result(it)


def _parse_loc_only(path: Path, acc: LocAccumulator):
    """サブエージェント jsonl から編集ツールの行数だけ集計器へ流す（発話・トークンは数えない）。"""
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type")
            msg = rec.get("message") or {}
            if t == "user":
                _feed_tool_results(acc, msg.get("content"))
            elif t == "assistant":
                content = msg.get("content")
                if isinstance(content, list):
                    ts = rec.get("timestamp")
                    dt = _to_local_dt(ts) if isinstance(ts, str) else None
                    day = dt.strftime("%Y-%m-%d") if dt else None
                    for it in content:
                        if isinstance(it, dict) and it.get("type") == "tool_use":
                            acc.on_tool_use(it, day)


def _subagent_files(session_jsonl: Path):
    subdir = session_jsonl.parent / session_jsonl.stem
    if not subdir.is_dir():
        return []
    return sorted(subdir.rglob("agent-*.jsonl"))


def count_subagents(session_jsonl: Path) -> int:
    """サブエージェント記録（<session-id>/subagents/agent-*.jsonl）を数える。"""
    subdir = session_jsonl.parent / session_jsonl.stem
    if not subdir.is_dir():
        return 0
    return sum(1 for _ in subdir.rglob("agent-*.jsonl"))
