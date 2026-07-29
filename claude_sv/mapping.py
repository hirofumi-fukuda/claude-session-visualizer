"""セッション→案件マッピング（採番規約は config に外出し・汎用コア）。

判定の優先順位:
  ① kickoff_patterns（採番合言葉）を ai-title → 冒頭発話プール → last-prompt に照合
  ② title_hints（タイトル/冒頭発話のキーワード含有 → 案件。業務ハブ等の非採番セッション用）
  ③ workspace_hints（専用リポジトリのワークスペース → 案件）
どれにもヒットしなければ未分類。
案件名の解決は「別名の完全一致 → 捕捉文字列内の最長別名部分一致」の2段階
（例:「たとえば「GEMiNX Post」→ GEMiNX Post、「ホットフォルダー脱却プロジェクト」→ ホットフォルダー脱却）。
"""
import json
import re
from pathlib import Path


def load_config(repo_root: Path) -> dict:
    """config.local.json（個人設定）優先、なければ config.example.json。"""
    for name in ("config.local.json", "config.example.json"):
        p = repo_root / name
        if p.exists():
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["_config_path"] = str(p)
            return cfg
    raise FileNotFoundError("config.local.json / config.example.json が見つからない")


class ProjectMapper:
    def __init__(self, cfg: dict):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in cfg.get("kickoff_patterns", [])]
        self.alias_to_canonical = {}
        for canonical, aliases in cfg.get("project_aliases", {}).items():
            for a in [canonical, *aliases]:
                self.alias_to_canonical[self._norm(a)] = canonical
        # 部分一致用: 正規化別名を長い順に並べる（最長一致優先）
        self.aliases_by_len = sorted(self.alias_to_canonical, key=len, reverse=True)
        self.title_hints = cfg.get("title_hints", [])
        self.workspace_hints = cfg.get("workspace_hints", {})

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s).casefold()

    def _resolve_project(self, raw: str) -> str:
        key = self._norm(raw)
        if key in self.alias_to_canonical:
            return self.alias_to_canonical[key]
        # 案件名は捕捉文字列の末尾に来ることが多い（例:「KANADEMONO ウエルカムDM」）ので
        # 末尾一致を最優先、なければ最長部分一致
        contained = [a for a in self.aliases_by_len if len(a) >= 3 and a in key]
        if contained:
            ending = [a for a in contained if key.endswith(a)]
            return self.alias_to_canonical[(ending or contained)[0]]
        return raw.strip()

    def _try_match(self, text: str):
        for pat in self.patterns:
            m = pat.search(text)
            if m:
                raw = m.groupdict().get("project", "").strip()
                n = m.groupdict().get("n")
                return self._resolve_project(raw), (int(n) if n else None)
        return None

    def map_session(self, summary: dict) -> dict:
        """summary に project / session_no / match_source を付与して返す。"""
        candidates = [
            ("ai-title", [summary.get("ai_title") or ""]),
            ("first-prompts", summary.get("user_texts_matchpool") or []),
            ("last-prompt", [summary.get("last_prompt") or ""]),
        ]
        for source, texts in candidates:
            for text in texts:
                hit = self._try_match(text)
                if hit:
                    summary["project"], summary["session_no"] = hit
                    summary["match_source"] = source
                    return summary

        # キーワードヒント（業務ハブ等の非採番セッション）
        hint_texts = self._norm(
            (summary.get("ai_title") or "") + " " + (summary.get("first_user_text") or "")
        )
        for hint in self.title_hints:
            if any(self._norm(kw) in hint_texts for kw in hint.get("contains", [])):
                summary["project"] = hint["project"]
                summary["session_no"] = None
                summary["match_source"] = "title-hint"
                return summary

        ws_project = self.workspace_hints.get(summary.get("workspace"))
        if ws_project:
            summary["project"] = ws_project
            summary["session_no"] = None
            summary["match_source"] = "workspace"
            return summary

        summary["project"] = None
        summary["session_no"] = None
        summary["match_source"] = None
        return summary
