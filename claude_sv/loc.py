"""Claude が書いた行数（LOC）の集計（汎用コア・読み取り専用）。

jsonl の tool_use（Edit/Write/MultiEdit/NotebookEdit・Bash/PowerShell のヒアドキュメント書き込み）を
tool_result と突き合わせ、**成功したものだけ**を数える。1セッション（＋そのサブエージェント）を
LocAccumulator 1つで受け、parser.parse_session が summary に畳む。

行数の定義（＝Anthropic 管理コンソールの「コード行数」とは別物・社内比較用の自前指標）:
  Write         = content の行数（上書きでも全行を「書いた」と数える）
  Edit          = new_string の行数を added・old_string の行数を removed
  MultiEdit     = edits の合計
  NotebookEdit  = new_source の行数
  Bash/PowerShell
    `cat > path <<'EOF' … EOF`／`@' … '@ | Set-Content path` のような**ファイル直書き**は本文行数を数える
    `sed -i`／`python - <<PY`（中で open(...,"w")）／`tee`／`Out-File` 等の**間接編集は行数が取れない**ので
    「未計測の編集コマンド」として**件数だけ**数える（unmeasured）
  失敗した tool_result（is_error）は数えない。稼働中で結果未着のものも数えない。

分類: 拡張子で code / docs / other（docs = md/txt/csv/log 等の文章・記録。設定JSON等は code 側）。
"""
import os
import re

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

CODE_EXT = {
    ".cs", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sql", ".ps1", ".psm1", ".sh",
    ".bat", ".cmd", ".cshtml", ".razor", ".html", ".htm", ".css", ".scss", ".json", ".jsonc",
    ".yml", ".yaml", ".xml", ".csproj", ".sln", ".config", ".ipynb", ".vue", ".go", ".rb",
    ".java", ".kt", ".swift", ".toml", ".ini", ".env", ".proto", ".graphql", ".tf",
}
DOC_EXT = {".md", ".markdown", ".txt", ".csv", ".tsv", ".log", ".rst", ".adoc", ".vtt", ".srt"}


def classify(file_path) -> str:
    """ファイルパス → "code" / "docs" / "other"。"""
    if not file_path:
        return "other"
    ext = os.path.splitext(str(file_path))[1].lower()
    if ext in CODE_EXT:
        return "code"
    if ext in DOC_EXT:
        return "docs"
    return "other"


def _nlines(s) -> int:
    if not s or not isinstance(s, str):
        return 0
    return len(s.splitlines())


def calc_edit(name: str, inp) -> tuple:
    """編集系ツール → (added, removed, file_path)。"""
    if not isinstance(inp, dict):
        return (0, 0, None)
    if name == "Write":
        return (_nlines(inp.get("content")), 0, inp.get("file_path"))
    if name == "Edit":
        return (_nlines(inp.get("new_string")), _nlines(inp.get("old_string")), inp.get("file_path"))
    if name == "MultiEdit":
        a = r = 0
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                a += _nlines(e.get("new_string"))
                r += _nlines(e.get("old_string"))
        return (a, r, inp.get("file_path"))
    if name == "NotebookEdit":
        return (_nlines(inp.get("new_source")), 0, inp.get("notebook_path"))
    return (0, 0, None)


# ── シェル（Bash/PowerShell）のヒューリスティック ─────────────────────
# ヒアドキュメント: `<<'EOF' … EOF`（引用符の有無・<<- を許容）
_HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
# ヒアドキュメント行のファイルリダイレクト先（`2>` や /dev/null は除外）
_REDIRECT_RE = re.compile(r"(?<![<>0-9&])>{1,2}\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;|&<>]+))")
# PowerShell ヒアストリング `@' … '@`
_PS_HERESTR_RE = re.compile(r"@(['\"])\r?\n(.*?)\r?\n\1@", re.S)
_PS_WRITE_RE = re.compile(
    r"(?:Set-Content|Out-File|Add-Content)\s+(?:-(?:Literal)?Path\s+|-FilePath\s+)?"
    r"(?:\"([^\"]+)\"|'([^']+)'|([^\s;|]+))", re.I)
# 行数が取れない編集コマンド（件数だけ数える）
_UNMEASURED_RE = re.compile(
    r"\bsed\s+(?:-[a-zA-Z]*i|--in-place)|\btee\b|\bSet-Content\b|\bOut-File\b|\bAdd-Content\b"
    r"|\bpatch\b\s+-|\bgit\s+apply\b|\bpython3?\s+-c\b.*?(?:open\(.*?['\"][wa]|write_text|write_bytes)",
    re.I | re.S)
# ヒアドキュメントで渡したスクリプトの中のファイル書き込み
_SCRIPT_WRITE_RE = re.compile(
    r"open\([^)]*['\"][wa]b?\+?['\"]|write_text\(|write_bytes\(|\bsed\s+-i|Set-Content|Out-File"
    r"|\bcat\s*>|\btee\b", re.S)


def calc_shell(cmd) -> tuple:
    """シェルコマンド → (measured=[(lines, file_path), ...], unmeasured: 0/1)。"""
    if not isinstance(cmd, str) or not cmd:
        return ([], 0)
    measured = []
    script_writes = False
    for m in _HEREDOC_RE.finditer(cmd):
        line_start = cmd.rfind("\n", 0, m.start()) + 1
        line_end = cmd.find("\n", m.start())
        head = cmd[line_start:line_end if line_end >= 0 else len(cmd)]
        body = m.group(3)
        target = None
        for r in _REDIRECT_RE.finditer(head):
            cand = r.group(1) or r.group(2) or r.group(3)
            if cand and "/dev/null" not in cand and cand not in ("&1", "&2"):
                target = cand
                break
        if target:
            measured.append((_nlines(body), target))
        elif _SCRIPT_WRITE_RE.search(body):
            script_writes = True
    for m in _PS_HERESTR_RE.finditer(cmd):
        tail = cmd[m.end():m.end() + 300]
        w = _PS_WRITE_RE.search(tail)
        if w:
            measured.append((_nlines(m.group(2)), w.group(1) or w.group(2) or w.group(3)))
    if measured:
        return (measured, 0)
    if script_writes or _UNMEASURED_RE.search(cmd):
        return ([], 1)
    return ([], 0)


class LocAccumulator:
    """tool_use を pending に積み、成功 tool_result で確定する。"""

    def __init__(self):
        self.pending = {}   # tool_use_id -> (day, [(added, removed, file_path, via_shell)], unmeasured)
        self.added = self.removed = 0
        self.by_kind = {"code": 0, "docs": 0, "other": 0}
        self.bash_added = 0
        self.unmeasured = 0
        self.n_edits = 0
        self.days = {}      # day -> {"code","docs","other","removed"}
        self.files = {}     # basename -> added

    def on_tool_use(self, item: dict, day):
        name, inp, tid = item.get("name"), item.get("input"), item.get("id")
        if not tid:
            return
        if name in EDIT_TOOLS:
            a, r, fp = calc_edit(name, inp)
            if a or r:
                self.pending[tid] = (day, [(a, r, fp, False)], 0)
        elif name in SHELL_TOOLS and isinstance(inp, dict):
            measured, unm = calc_shell(inp.get("command"))
            if measured or unm:
                self.pending[tid] = (day, [(a, 0, fp, True) for a, fp in measured], unm)

    def on_tool_result(self, item: dict):
        tid = item.get("tool_use_id")
        if tid not in self.pending:
            return
        day, edits, unm = self.pending.pop(tid)
        if item.get("is_error"):
            return
        content = item.get("content")
        if isinstance(content, str) and content.startswith("<tool_use_error>"):
            return
        if unm:
            self.unmeasured += unm
        for a, r, fp, via_shell in edits:
            self.n_edits += 1
            kind = classify(fp)
            self.added += a
            self.removed += r
            self.by_kind[kind] += a
            if via_shell:
                self.bash_added += a
            d = self.days.setdefault(day or "", {"code": 0, "docs": 0, "other": 0, "removed": 0})
            d[kind] += a
            d["removed"] += r
            if fp:
                base = os.path.basename(str(fp).replace("\\", "/"))
                self.files[base] = self.files.get(base, 0) + a

    def result(self) -> dict:
        top = dict(sorted(self.files.items(), key=lambda kv: -kv[1])[:30])
        return {
            "loc": {
                "added": self.added, "removed": self.removed,
                "code": self.by_kind["code"], "docs": self.by_kind["docs"],
                "other": self.by_kind["other"],
                "bash_added": self.bash_added, "unmeasured": self.unmeasured,
                "n_edits": self.n_edits,
            },
            "days_loc": {k: v for k, v in self.days.items() if k},
            "files_loc": top,
        }
