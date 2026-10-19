"""モデル系統バッジ（3点セット共通・S14）。

live_board の _peek_model / parser の model が拾った値を系統色バッジにする。
色は「識別」にだけ使い、系統名（Opus/Sonnet/Haiku/Fable）を必ず併記
＝色だけに意味を載せない。ダークは明色文字、ライトは文字色だけ濃色に
落とす（淡背景での可読性）。CSS は各ページ共通の変数名 --muted/--wash/--border に依存。
"""
import html as html_mod
import re

MODEL_FAMILY = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable"}

MDL_CSS = """
/* モデルバッジ: 系統ごとに色分け。系統名併記＝色だけに意味を載せない（claude_sv/modelbadge.py 共有） */
.mdl{display:inline-block;font-size:10.5px;font-weight:600;line-height:1.7;
  padding:0 8px;border-radius:999px;border:1px solid;white-space:nowrap}
.mdl-opus{color:#e0648c;background:rgba(224,100,140,.14);border-color:rgba(224,100,140,.36)}
.mdl-sonnet{color:#4a90e2;background:rgba(74,144,226,.14);border-color:rgba(74,144,226,.36)}
.mdl-haiku{color:#d99423;background:rgba(217,148,35,.14);border-color:rgba(217,148,35,.36)}
.mdl-fable{color:#b083f0;background:rgba(176,131,240,.14);border-color:rgba(176,131,240,.36)}
.mdl-unk{color:var(--muted);background:var(--wash);border-color:var(--border)}
/* ライトモードは文字色だけ濃色へ（淡背景での可読性・S14）。背景/枠の色相は共通 */
:root[data-theme="light"] .mdl-opus{color:#c01a5e}
:root[data-theme="light"] .mdl-sonnet{color:#2563eb}
:root[data-theme="light"] .mdl-haiku{color:#b45309}
:root[data-theme="light"] .mdl-fable{color:#7c3aed}
"""


def shorten(model):
    """生の model id → 短縮形（claude-fable-5 → fable-5 / claude-haiku-4-5-20251001 → haiku-4-5）。

    _peek_model の短縮形をそのまま渡しても同じ値が返る（冪等）。
    """
    if not model:
        return ""
    m = re.sub(r"^claude-", "", model.strip())
    m = re.sub(r"-20\d{6}$", "", m)
    return re.sub(r"\[[^\]]*\]$", "", m)


def model_badge(model, note=None):
    """model id（生/短縮どちらでも）→ バッジ HTML。空・不明は空文字。

    note はツールチップの補足（例:「直近セッションのモデル」）。
    """
    model = shorten(model)
    if not model:
        return ""
    family = model.split("-", 1)[0]
    label_base = MODEL_FAMILY.get(family)
    cls = family if label_base else "unk"
    if not label_base:
        label_base = family.capitalize()
    ver = model[len(family) + 1:].replace("-", ".") if len(model) > len(family) + 1 else ""
    label = f"{label_base} {ver}".strip()
    title = f"{note}: {model}" if note else model
    esc = html_mod.escape
    return f'<span class="mdl mdl-{cls}" title="{esc(title)}">{esc(label)}</span>'
