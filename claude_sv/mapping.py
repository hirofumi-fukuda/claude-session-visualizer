"""セッション→案件マッピング（採番規約は config に外出し・汎用コア）。

判定の優先順位:
  ⓪ /rename 名（稼働台帳 sessions/*.json の name・nameSource=user のみ＝自動命名は無視）＝最優先
     （S23・2026-10-16。それまでは案件名＞/rename 名で「/rename しても席が変わらない」）。
     `〈トラック名〉S〈n〉` は末尾の S を剥がし、別名の**完全一致だけ**で案件名に正規化する
     （部分一致はしない＝略称 rename が広い案件〔TOPPAN 等〕に吸われないため）。当たらなければ
     名前そのまま＝相棒席（例「絵の作成」）。S が無ければ番号だけ ① の同一案件ヒットから拾う
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
    # /rename 名の「〈トラック名〉S〈n〉」（空白の有無は問わない・例「GEMiNX本体開発S15」「本体開発 S12」）
    RENAME_RX = re.compile(r"^\s*(?P<project>.+?)\s*[SsＳ]\s*(?P<n>\d{1,3})\s*$")

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

    def map_user_name(self, name: str):
        """/rename 名 → (案件名, S番号|None)。別名は完全一致だけ・当たらなければ名前そのまま。空なら None。"""
        name = (name or "").strip()
        if not name:
            return None
        m = self.RENAME_RX.match(name)
        raw, n = (m.group("project"), int(m.group("n"))) if m else (name, None)
        raw = raw.strip()
        return self.alias_to_canonical.get(self._norm(raw), raw), n

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
        # 案件は先勝ち（別案件の合言葉が後から出ても乗り換えない＝誤マッピング防止）。
        # 番号は同一案件の中で「後勝ち」＝
        #   ・番号なしヒット（例: ai-title「〜新セッション開始」）の後に番号付きが来れば番号を採用
        #     （起動後に採番合言葉を打つ遡及採番・2026-08-15）
        #   ・番号付きの後に同一案件の番号付きが来れば新しい方を採用
        #     （「S8 開始」で始めて台帳と食い違いに気づき「S18 開始」と打ち直す訂正採番・S18）
        project, session_no, match_source = None, None, None
        # ⓪ /rename 名が最優先（S23）。番号まで付いていれば確定。無ければ番号だけ下の合言葉から拾う
        hit = self.map_user_name(summary.get("user_name") or "")
        if hit:
            project, session_no, match_source = hit[0], hit[1], "user-rename"
            if session_no is not None:
                summary["project"], summary["session_no"] = project, session_no
                summary["match_source"] = match_source
                return summary
        for source, texts in candidates:
            for text in texts:
                hit = self._try_match(text)
                if not hit:
                    continue
                if project is None:
                    project, session_no, match_source = hit[0], hit[1], source
                elif hit[0] == project and hit[1] is not None:
                    session_no, match_source = hit[1], source
        if project is not None:
            summary["project"], summary["session_no"] = project, session_no
            summary["match_source"] = match_source
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
