"""オフィス配置図（reports/office.html）のキャラ絵サンプルを、コード内のピクセル絵（12×16 マス）から書き出す。

絵の正本は themes/*.py（1テーマ＝人間＋エージェント4モデル分）。このスクリプトは描画と一覧化だけ。

使い方:
  python gen.py                     全テーマを out/<theme>/ に書き出し＋一覧 sheet.png
  python gen.py --theme ninja       1テーマだけ（out/ninja/ と out/ninja/sheet.png）
  python gen.py --install ninja     out/ninja/ の絵を assets/sprites/ninja/ に置く（office.html のテーマ切替に出る）
  python gen.py --install-all       全テーマを assets/sprites/<theme>/ に置く
  python gen.py --default ninja [--human 2]   assets/sprites/ 直下の固定名（＝既定の絵）に置く。人間は 2 番目の候補

書き出すファイル（受け入れ仕様＝../README.md）:
  human.png            人間（候補が複数なら human-2.png, human-3.png ...）
  agent-<model>.png    モデル別（opus / sonnet / haiku / fable）。色分け＝下の MODEL_COLORS
  agent.png            共通のグレー版。モデル不明の席で office.py が乗算で色を乗せる用（全体が単色に染まる）
PNG は 36×48（1マス＝3px）。表示サイズと同じなので image-rendering が効かない環境でもにじまない。
PNG には外側 1px の薄い白フチ（RIM）を焼き込む。黒髪・黒い服が office の暗い床に溶けないように。
"""
import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.dont_write_bytecode = True  # themes/ に __pycache__ を作らない

HERE = Path(__file__).resolve().parent
THEME_DIR = HERE / "themes"
OUT_DIR = HERE / "out"
INSTALL_DIR = HERE.parent  # assets/sprites/

W, H = 12, 16
SCALE = 3  # 書き出し PNG の 1 マス（36×48）
RIM = (255, 255, 255, 90)  # 書き出し PNG の外側 1px のフチ（白 35%）。None で無し

# office.py の MODEL_COL と同じ色。unknown＝モデル不明時に agent.png へ乗算される色
MODEL_COLORS = {"opus": "#8a5cf6", "sonnet": "#3b82f6", "haiku": "#d4a017", "fable": "#f472b6"}
UNKNOWN_COLOR = "#c24b4b"
MODELS = list(MODEL_COLORS)
MODEL_LABEL = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable"}

# 全テーマ共通の色。テーマ側の PAL で上書き・追加できる。
# 1/2/3 は予約＝モデル色（1=本体・2=影・3=ハイライト）。人間の絵では使わない。
BASE_PAL = {
    "s": "#f2c9a0",  # 肌
    "S": "#d9a57a",  # 肌の影
    "p": "#f29a9a",  # ほっぺ
    "e": "#26232b",  # 目・ごく濃い線
    "w": "#ffffff",
    "W": "#d8dde6",  # 明るいグレー
    "g": "#9aa3b2",  # グレー
    "G": "#5d6577",  # 濃いグレー
    "k": "#2b2b33",  # 黒寄り（髪・靴）
    "K": "#444a58",  # ズボン
    "r": "#d64545",  # 赤
    "y": "#f2c94c",  # 黄
    "b": "#8b5a2b",  # 茶
    "B": "#5e3a1a",  # こげ茶
    "f": "#2e9e6b",  # 人間の既定シャツ（凡例の 👤 色）
    "F": "#1f7a51",  # 同・影
}
MODEL_CODES = "123"


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def model_shades(hex_color: str) -> dict:
    c = _hex(hex_color)
    return {"1": c, "2": _mix(c, (0, 0, 0), 0.30), "3": _mix(c, (255, 255, 255), 0.38)}


# agent.png（グレー版）でのモデル色の置き換え先。乗算で着色されるので本体は明るめ
GRAY_MODEL = {"1": (226, 226, 226), "2": (160, 160, 160), "3": (255, 255, 255)}


def load_themes(only=None):
    themes = []
    for f in sorted(THEME_DIR.glob("*.py")):
        # ファイル名＝テーマ名。--theme の時は他テーマのファイルを読まない（書きかけでも巻き込まれない）
        if f.name.startswith("_") or (only and f.stem != only):
            continue
        spec = importlib.util.spec_from_file_location(f"theme_{f.stem}", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.NAME = f.stem
        themes.append(mod)
    themes.sort(key=lambda m: (getattr(m, "ORDER", 99), getattr(m, "NAME", "")))
    return themes


def check_grid(grid, pal, where):
    """16 行×12 列・未定義の文字なし、を確かめる（間違いは場所つきで止める）。"""
    errs = []
    if len(grid) != H:
        errs.append(f"{where}: 行数 {len(grid)}（16 行のはず）")
    for r, row in enumerate(grid):
        if len(row) != W:
            errs.append(f"{where}: {r} 行目の幅 {len(row)}（12 のはず）: {row!r}")
        for c, ch in enumerate(row):
            if ch != "." and ch not in pal and ch not in MODEL_CODES:
                errs.append(f"{where}: {r} 行 {c} 列の文字 {ch!r} が PAL に無い")
    if errs:
        raise SystemExit("\n".join(errs))


def render(grid, pal, model=None, gray=False, scale=1) -> Image.Image:
    """grid（12×16 の文字列）→ RGBA。model を渡すと 1/2/3 をその色に、gray=True は agent.png 用の白黒。"""
    shades = model_shades(MODEL_COLORS[model]) if model else {}
    img = Image.new("RGBA", (W * scale, H * scale), (0, 0, 0, 0))
    px = ImageDraw.Draw(img)
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch == ".":
                continue
            if ch in MODEL_CODES:
                rgb = GRAY_MODEL[ch] if gray else shades.get(ch, GRAY_MODEL[ch])
            else:
                rgb = _hex(pal[ch])
                if gray:
                    v = round(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2])
                    rgb = (v, v, v)
            px.rectangle([c * scale, r * scale, (c + 1) * scale - 1, (r + 1) * scale - 1], fill=rgb + (255,))
    return img


def tint_multiply(img: Image.Image, hex_color: str) -> Image.Image:
    """office.py の feFlood→feComposite(in)→feBlend(multiply) と同じ見え方（一覧での確認用）。"""
    t = _hex(hex_color)
    out = img.copy()
    p = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = p[x, y]
            if a:
                p[x, y] = (r * t[0] // 255, g * t[1] // 255, b * t[2] // 255, a)
    return out


def add_rim(img: Image.Image) -> Image.Image:
    """シルエットの外側 1px に RIM を足す（キャンバスの外にはみ出す分は切れる）。"""
    if not RIM:
        return img
    a = img.getchannel("A").point(lambda v: 255 if v else 0)
    edge = ImageChops.subtract(a.filter(ImageFilter.MaxFilter(3)), a)
    out = Image.new("RGBA", img.size, RIM[:3] + (0,))
    out.putalpha(edge.point(lambda v: RIM[3] if v else 0))
    out.alpha_composite(img)
    return out


def theme_parts(mod):
    """テーマ → (pal, humans[(label, grid)], agents{model: (label, grid)}, fallback(label, grid))"""
    pal = dict(BASE_PAL)
    pal.update(getattr(mod, "PAL", {}))
    humans = mod.HUMAN if isinstance(mod.HUMAN, list) else [mod.HUMAN]
    raw = mod.AGENTS
    if "*" in raw:
        agents = {m: raw.get(m, raw["*"]) for m in MODELS}
        fallback = raw["*"]
    else:
        missing = [m for m in MODELS if m not in raw]
        if missing:
            raise SystemExit(f"{mod.NAME}: AGENTS に {missing} が無い（4モデル分か '*' を置く）")
        agents = {m: raw[m] for m in MODELS}
        fallback = getattr(mod, "FALLBACK", None) or raw["opus"]
    for i, (label, grid) in enumerate(humans):
        check_grid(grid, pal, f"{mod.NAME} HUMAN[{i}] {label}")
    for m, (label, grid) in agents.items():
        check_grid(grid, pal, f"{mod.NAME} AGENTS[{m}] {label}")
    check_grid(fallback[1], pal, f"{mod.NAME} FALLBACK")
    return pal, humans, agents, fallback


def write_theme(mod):
    pal, humans, agents, fallback = theme_parts(mod)
    d = OUT_DIR / mod.NAME
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.png"):
        old.unlink()
    for i, (label, grid) in enumerate(humans):
        add_rim(render(grid, pal, scale=SCALE)).save(d / ("human.png" if i == 0 else f"human-{i + 1}.png"))
    for m, (label, grid) in agents.items():
        add_rim(render(grid, pal, model=m, scale=SCALE)).save(d / f"agent-{m}.png")
    add_rim(render(fallback[1], pal, gray=True, scale=SCALE)).save(d / "agent.png")
    return pal, humans, agents, fallback


# ---------- 一覧シート ----------
SHEET_BG = (27, 29, 42)        # #1b1d2a（office の背景）
CARD_BG = (42, 45, 63)         # #2a2d3f（床タイル）
CARD_BG2 = (47, 51, 72)        # #2f3348
INK = (230, 232, 240)
INK2 = (150, 156, 175)
ZOOM = 6                        # 一覧での拡大率（1マス＝6px）
CARD_W, CARD_H = W * ZOOM + 16, H * ZOOM + 16


def _font(size, bold=False):
    names = ["YuGothB.ttc", "meiryob.ttc"] if bold else ["YuGothM.ttc", "meiryo.ttc"]
    for n in names + ["msgothic.ttc"]:
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{n}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def _label(d, x, y, text, font, fill, maxw):
    """カード幅を超える名前は折り返す（隣のカードに食い込ませない）。
    「（」があればその手前で切る。閉じ括弧・句読点は行頭に送らない。"""
    lines, cur = [], ""
    for ch in text:
        if cur and d.textlength(cur + ch, font=font) > maxw:
            i = cur.rfind("（")
            if i > 0:
                lines.append(cur[:i])
                cur = cur[i:]
            elif ch not in "）)」、。":
                lines.append(cur)
                cur = ""
        cur += ch
    for k, line in enumerate(lines + [cur]):
        d.text((x, y + k * 13), line, font=font, fill=fill)


def _card(sheet, x, y, sprite, accent=None):
    d = ImageDraw.Draw(sheet)
    d.rectangle([x, y, x + CARD_W - 1, y + CARD_H - 1], fill=CARD_BG)
    if accent:
        d.rectangle([x, y, x + CARD_W - 1, y + 3], fill=_hex(accent))
    sheet.alpha_composite(sprite, (x + 8, y + 8))


def _desk_scene(sprites, human):
    """office.py の desk() と同じ寸法で、実寸（36×48）の見え方を1枚にする。"""
    w_, h_ = 340, 150
    img = Image.new("RGBA", (w_, h_), CARD_BG + (255,))
    d = ImageDraw.Draw(img)
    for ty in range(0, h_, 40):
        for tx in range(0, w_, 40):
            d.rectangle([tx, ty, tx + 19, ty + 19], fill=CARD_BG2)
            d.rectangle([tx + 20, ty + 20, tx + 39, ty + 39], fill=CARD_BG2)

    def desk(cx, cy, pair, screen):
        dw, dh = 120, 40
        for i, spr in enumerate(pair):
            img.alpha_composite(spr, (cx - dw // 2 + 14 + i * 44, cy - 62))
        d.rectangle([cx - dw // 2, cy, cx + dw // 2 - 1, cy + dh - 1], fill=(139, 90, 43))
        d.rectangle([cx - dw // 2, cy, cx + dw // 2 - 1, cy + 7], fill=(181, 122, 62))
        d.rectangle([cx - 16, cy + 10, cx + 15, cy + 31], fill=(17, 17, 17))
        d.rectangle([cx - 14, cy + 12, cx + 13, cy + 29], fill=screen)

    desk(75, 80, sprites[:2], (57, 211, 83))
    desk(230, 80, sprites[2:4], (74, 163, 255))
    if human is not None:
        img.alpha_composite(human, (230 + 60 + 10 - 4, 80 - 50))
    return img


def make_sheet(theme_rows, path: Path):
    """theme_rows: [(mod, pal, humans, agents, fallback)] → 一覧 PNG"""
    f_title, f_lab, f_small = _font(20, True), _font(13), _font(11)
    col_gap = 10
    n_cols_max = max(len(h) for _, _, h, _, _ in theme_rows) + len(MODELS) + 1
    left = 18
    row_h = 44 + CARD_H + 44
    scene_w = 340
    width = left + n_cols_max * (CARD_W + col_gap) + 20 + scene_w + 20
    height = 70 + len(theme_rows) * row_h + 10
    sheet = Image.new("RGBA", (width, height), SHEET_BG + (255,))
    d = ImageDraw.Draw(sheet)
    d.text((left, 16), "オフィスのキャラ絵サンプル（12×16 ドット・一覧は 6 倍／右端は実寸 36×48 の見え方）", font=f_title, fill=INK)
    d.text((left, 44), "並び: 人間 → Opus → Sonnet → Haiku → Fable → 共通グレー版（モデル不明時＝赤で乗算した見え方）", font=f_small, fill=INK2)
    y = 70
    for mod, pal, humans, agents, fallback in theme_rows:
        d.text((left, y + 2), f"{mod.NAME}  {mod.TITLE}", font=f_lab, fill=INK)
        if getattr(mod, "NOTE", ""):
            d.text((left, y + 20), mod.NOTE, font=f_small, fill=INK2)
        cy = y + 40
        x = left
        for i, (label, grid) in enumerate(humans):
            _card(sheet, x, cy, render(grid, pal, scale=ZOOM), accent="#2e9e6b")
            d.text((x, cy + CARD_H + 4), ("人間" if i == 0 else f"人間{i + 1}"), font=f_small, fill=INK2)
            _label(d, x, cy + CARD_H + 18, label, f_small, INK, CARD_W + col_gap - 4)
            x += CARD_W + col_gap
        for m in MODELS:
            label, grid = agents[m]
            _card(sheet, x, cy, render(grid, pal, model=m, scale=ZOOM), accent=MODEL_COLORS[m])
            d.text((x, cy + CARD_H + 4), MODEL_LABEL[m], font=f_small, fill=_hex(MODEL_COLORS[m]))
            _label(d, x, cy + CARD_H + 18, label, f_small, INK, CARD_W + col_gap - 4)
            x += CARD_W + col_gap
        g = tint_multiply(render(fallback[1], pal, gray=True, scale=ZOOM), UNKNOWN_COLOR)
        _card(sheet, x, cy, g, accent=UNKNOWN_COLOR)
        d.text((x, cy + CARD_H + 4), "共通(不明)", font=f_small, fill=INK2)
        _label(d, x, cy + CARD_H + 18, fallback[0], f_small, INK, CARD_W + col_gap - 4)
        # 実寸シーン
        sx = left + n_cols_max * (CARD_W + col_gap) + 20
        smalls = [add_rim(render(agents[m][1], pal, model=m, scale=SCALE)) for m in MODELS]
        scene = _desk_scene(smalls, add_rim(render(humans[0][1], pal, scale=SCALE)))
        sheet.alpha_composite(scene, (sx, cy))  # NOTE 行（y+20）と縦に重ならない位置
        y += row_h
    sheet.save(path)


def _out(theme: str) -> Path:
    src = OUT_DIR / theme
    if not src.is_dir():
        raise SystemExit(f"{src} が無い（先に python gen.py --theme {theme}）")
    return src


def install(theme: str):
    """out/<theme>/ の絵（sheet.png 以外）を assets/sprites/<theme>/ に置く。人間の候補 human-N.png も全部。"""
    src, dest = _out(theme), INSTALL_DIR / theme
    dest.mkdir(exist_ok=True)
    for s in sorted(src.glob("*.png")):
        if s.name != "sheet.png":
            shutil.copyfile(s, dest / s.name)
    print(f"installed {theme}/ <- out/{theme}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme")
    ap.add_argument("--install")
    ap.add_argument("--install-all", action="store_true")
    ap.add_argument("--default")
    ap.add_argument("--human", type=int)
    a = ap.parse_args()

    if a.human and not a.default:
        raise SystemExit("--human は --default 用（--install は人間の候補を全部置く）")
    if a.install or a.install_all:
        names = [a.install] if a.install else [f.stem for f in sorted(THEME_DIR.glob("*.py")) if not f.name.startswith("_")]
        for name in names:
            install(name)
        return
    if a.default:
        src, n = _out(a.default), a.human or 1
        human = src / ("human.png" if n == 1 else f"human-{n}.png")
        if not human.exists():
            raise SystemExit(f"{a.default} に人間の候補 {n} が無い")
        pairs = [(human, INSTALL_DIR / "human.png"), (src / "agent.png", INSTALL_DIR / "agent.png")]
        pairs += [(src / f"agent-{m}.png", INSTALL_DIR / f"agent-{m}.png") for m in MODELS]
        for s, t in pairs:
            shutil.copyfile(s, t)
            print(f"installed {t.name} <- {a.default}/{s.name}")
        return

    themes = load_themes(a.theme)
    if not themes:
        raise SystemExit(f"テーマ {a.theme!r} が themes/ に無い")
    rows = [(mod,) + write_theme(mod) for mod in themes]
    if a.theme:
        make_sheet(rows, OUT_DIR / a.theme / "sheet.png")
        print(f"wrote out/{a.theme}/ and out/{a.theme}/sheet.png")
    else:
        for row in rows:
            make_sheet([row], OUT_DIR / row[0].NAME / "sheet.png")
        make_sheet(rows, HERE / "sheet.png")
        print(f"wrote {len(rows)} themes to out/ and sheet.png")


if __name__ == "__main__":
    main()
