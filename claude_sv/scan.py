"""~/.claude/projects 配下の全セッションを走査（mtime+size 差分キャッシュ付き・汎用コア）。"""
import json
from pathlib import Path

from .parser import parse_session, count_subagents

CACHE_VERSION = 3  # parser の出力形式を変えたら上げる（全再パース）。3: model 追加


def scan_all(claude_dir: Path, cache_path: Path, workspaces: list[str] | None = None,
             progress=None) -> list[dict]:
    """全ワークスペースのセッション jsonl をパースしてサマリー一覧を返す。

    キャッシュ: {path: {mtime, size, cache_version, summary}}。
    mtime と size が一致すればパースを省略（稼働中セッションは自然に再パースされる）。
    """
    cache = {}
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}

    projects_dir = claude_dir / "projects"
    results = []
    new_cache = {}
    n_parsed = 0
    for ws_dir in sorted(projects_dir.iterdir()):
        if not ws_dir.is_dir():
            continue
        if workspaces and ws_dir.name not in workspaces:
            continue
        for jsonl in sorted(ws_dir.glob("*.jsonl")):
            key = str(jsonl)
            st = jsonl.stat()
            cached = cache.get(key)
            if (cached and cached.get("mtime") == st.st_mtime
                    and cached.get("size") == st.st_size
                    and cached.get("cache_version") == CACHE_VERSION):
                summary = cached["summary"]
            else:
                summary = parse_session(jsonl)
                n_parsed += 1
                if progress and n_parsed % 20 == 0:
                    progress(f"  parsed {n_parsed} files...")
            # サブエージェント数は安価なので毎回取り直す（稼働中に増えるため）
            summary["n_subagents"] = count_subagents(jsonl)
            summary["workspace"] = ws_dir.name
            new_cache[key] = {
                "mtime": st.st_mtime, "size": st.st_size,
                "cache_version": CACHE_VERSION, "summary": summary,
            }
            results.append(summary)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(new_cache, f, ensure_ascii=False)
    if progress:
        progress(f"  total {len(results)} sessions ({n_parsed} parsed, {len(results) - n_parsed} from cache)")
    return results
