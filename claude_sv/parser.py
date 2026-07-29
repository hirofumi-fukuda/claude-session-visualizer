"""セッション jsonl のストリームパーサー（汎用コア・読み取り専用）。

1ファイル＝1セッション。1行ずつ読み、セッションのサマリー dict を返す。
稼働中セッションの追記途中行（不完全なJSON）は読み飛ばす。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

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


def _to_local_date(ts_iso: str) -> str | None:
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d")
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
        "cwd": None,
        "version": None,
        "days_active": {},  # "YYYY-MM-DD" -> assistantメッセージ数
        "parse_errors": 0,
    }
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
                if rec.get("isMeta") or rec.get("isSidechain"):
                    continue
                msg = rec.get("message") or {}
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
                if isinstance(ts, str):
                    day = _to_local_date(ts)
                    if day:
                        summary["days_active"][day] = summary["days_active"].get(day, 0) + 1
                msg = rec.get("message") or {}
                usage = msg.get("usage") or {}
                summary["output_tokens"] += usage.get("output_tokens") or 0
                summary["input_tokens"] += usage.get("input_tokens") or 0
                content = msg.get("content")
                if isinstance(content, list):
                    summary["n_tool_use"] += sum(
                        1 for it in content
                        if isinstance(it, dict) and it.get("type") == "tool_use"
                    )
    return summary


def count_subagents(session_jsonl: Path) -> int:
    """サブエージェント記録（<session-id>/subagents/agent-*.jsonl）を数える。"""
    subdir = session_jsonl.parent / session_jsonl.stem
    if not subdir.is_dir():
        return 0
    return sum(1 for _ in subdir.rglob("agent-*.jsonl"))
