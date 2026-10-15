"""☕ 休憩室の小物（reports/office.html）のドット絵を、コード内のグリッドから書き出す。

受け入れ仕様＝../README.md（名前・表示箱・1ドット＝4px）。絵の正本は下の PROPS（1文字＝1ドット・'.'＝透明・パレットは小物ごと）。

使い方:
  python gen.py             out/<名前>.png と out/preview.png（ライト/ダークの床に休憩室と同じ並びで置いた確認用・2倍）
  python gen.py --install   上に加えて ../<名前>.png（assets/props/ 直下）に置く＝office.html の仮描きと差し替わる

PNG は表示箱と同じ大きさ（1ドット＝4px を焼き込み・下揃え・左右中央）＝ meet の倍率 1 で置かれる。
輪郭は濃い線（k）＋外側 1px の薄い白フチ（RIM・キャラ絵と同じ）＝明るい床は線で・暗い床はフチで浮かない。
コード側（office.py drawLounge）が上に重ねる絵との位置合わせ。グリッドをずらすと合わなくなる:
  coffee   湯気＝箱の x38-47・y32-37 → カップは列 9-11・行 9-10（x37-49・y40-48）
  printer  用紙＝箱の x146・y46 の 24×6（右へ流れる）→ 排紙口＝行 10-11 の右端・トレイ天面＝行 12 の上端（y52）
"""
import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
INSTALL_DIR = HERE.parent  # assets/props/
SPRITE_DIR = HERE.parent.parent / "sprites"

DOT = 4
RIM = (255, 255, 255, 90)  # キャラ絵（assets/sprites/samples/gen.py）と同じ
OUTLINE = {"k": "#2b2b33"}  # 全小物共通の輪郭
WOOD = {"t": "#d9a066", "u": "#b07a45", "v": "#7f5330"}  # 天板・正面・影

PROPS = {
    # 2人掛けソファ（正面）29×13・右の座面にクッション
    "sofa": dict(box=(120, 56), pal={
        "a": "#6cc7b8", "b": "#3e9e90", "c": "#2c7468", "d": "#215a52",
        "z": "#f8de8a", "y": "#f2c94c", "Y": "#c99a2e",
    }, grid=[
        "...kkkkkkkkkkkkkkkkkkkkkkk...",
        "..kaaaaaaaaaaaaaaaaaaaaaaak..",
        "..kbbbbbbbbbbbcbbbbbbbbbbbk..",
        ".kkkkbbbbbbbbbcbbbkkkkbbkkkk.",
        "kaaakbbbbbbbbbcbbkzyyykbkaaak",
        "kabckcccccccccccckyyyYkckabck",
        "kabckaaaaaaaaacaakyyYYkakabck",
        "kabckbbbbbbbbbcbbbkkkkbbkabck",
        "kabckbbbbbbbbbcbbbbbbbbbkabck",
        "kabckccccccccccccccccccckabck",
        "kdddddddddddddddddddddddddddk",
        "kkkkkkkkkkkkkkkkkkkkkkkkkkkkk",
        "..kk.....................kk..",
    ]),
    # ローテーブル＋コーヒーカップ 2 つ（上端）13×12
    "table": dict(box=(56, 50), pal={**WOOD, "m": "#6b3e1f", "w": "#ffffff", "W": "#c9d1dc", "g": "#9aa3b2"}, grid=[
        ".kkkk..kkkk..",
        ".kmmkg.kmmkg.",
        ".kwWkg.kwWkg.",
        "kkkkkkkkkkkkk",
        "kttttttttttuk",
        "kuuuuuuuuuuvk",
        "kvvvvvvvvvvvk",
        "kkkkkkkkkkkkk",
        ".ktk.....ktk.",
        ".kuk.....kuk.",
        ".kvk.....kvk.",
        ".kkk.....kkk.",
    ]),
    # 観葉植物 8×17（島の角にも 0.7 倍で置かれる）
    "plant": dict(box=(36, 70), pal={
        "L": "#86d474", "l": "#45a84f", "n": "#2c7a3a", "s": "#6b4a2b",
        "P": "#e3936a", "p": "#c8643b", "q": "#94442a",
    }, grid=[
        "..kkk...",
        ".kLLlkk.",
        "kLLllLlk",
        "kLlllllk",
        "knlLLlnk",
        ".kLllllk",
        "kLLllnnk",
        "knllnnk.",
        ".knnnk..",
        "..ksk...",
        "..ksk...",
        "kkkkkkkk",
        "kPppppqk",
        "kkkkkkkk",
        ".kPppqk.",
        ".kPppqk.",
        "..kkkk..",
    ]),
    # カウンター＋コーヒーメーカー 22×19。本体は暗色＝コード側の湯気（#ddd）が見える
    "coffee": dict(box=(90, 80), pal={**WOOD,
        "h": "#5b616e", "c": "#3a3f4b", "d": "#272a33", "s": "#c3cad6", "S": "#8a93a3",
        "x": "#141519", "m": "#6b3e1f", "e": "#39d353", "B": "#4aa3ff", "j": "#a5d8ef",
        "w": "#ffffff", "W": "#c9d1dc",
    }, grid=[
        ".....kkkkkkkkkkk......",
        ".....kssssssssSk......",
        ".....khcccccccdk......",
        ".....khBBccccwdk......",
        ".....khBBccccedk......",
        ".....khcccccccdk......",
        ".....khxxxSxxxdk.kkkk.",
        ".....khxxxxxxxdk.kSSk.",
        "kkkk.khxxxxxxxdk.kjmk.",
        "kwWk.khxxwwWxxdk.kmmk.",
        "kwWk.khxxwWWxxdk.kmmk.",
        "kkkkkkSSSSSSSSSkkkkkkk",
        "kttttttttttttttttttttk",
        "kkkkkkkkkkkkkkkkkkkkkk",
        "kuuuuuvkuuuuuvkuuuuuvk",
        "kuuuuuvkuuuuuvkuuuuuvk",
        "kuuuuSvkuuuuSvkuuuuSvk",
        "kvvvvvvkvvvvvvkvvvvvvk",
        "kkkkkkkkkkkkkkkkkkkkkk",
    ]),
    # 自販機（正面）14×25・缶 3×3＋ボタン・取り出し口・右の黒点＝硬貨口
    "vending": dict(box=(60, 104), pal={
        "r": "#d64545", "R": "#9e2f2f", "q": "#ec7d7d", "n": "#1e293b",
        "y": "#f2c94c", "Y": "#c99a2e", "b": "#4a90f0", "B": "#1d4ed8",
        "g": "#3ccf6e", "G": "#15803d", "o": "#f9873a", "O": "#c2410c",
        "p": "#ef86dd", "P": "#a83f96", "c": "#5ad1e6", "x": "#111318", "s": "#c3cad6",
        "w": "#ffffff", "W": "#d8dde6",
    }, grid=[
        ".kkkkkkkkkkkk.",
        "kqrrrrrrrrrrRk",
        "kqwwwwwwwwwwRk",
        "kqWWWrrrrWWWRk",
        "kqkkkkkkkkkkRk",
        "kqkyYnbBngGkRk",
        "kqkyYnbBngGkRk",
        "kqkncnncnnckRk",
        "kqkoOnpPnyYkxk",
        "kqkoOnpPnyYkRk",
        "kqkncnncnnckRk",
        "kqkbBngGnoOkRk",
        "kqkbBngGnoOkRk",
        "kqkncnncnnckRk",
        "kqkkkkkkkkkkRk",
        "kqrrrrrrrrrrRk",
        "kqrrrrrrrrrrRk",
        "kqkkkkkkkkkkRk",
        "kqkxxxxxxxxkRk",
        "kqkxxxxxxxxkRk",
        "kqksssssssskRk",
        "kqkkkkkkkkkkRk",
        "kqrrrrrrrrrrRk",
        "kRRRRRRRRRRRRk",
        "kkkkkkkkkkkkkk",
    ]),
    # 業務用プリンタ（iX っぽい横長の箱）43×24・左に信号灯・右上に操作パネル・右端に排紙トレイ
    "printer": dict(box=(176, 100), pal={
        "w": "#f4f5f7", "W": "#d5dae3", "g": "#9aa3b2", "G": "#5d6577", "c": "#3a3f4b", "x": "#16181d",
        "B": "#4aa3ff", "j": "#a5d8ff", "v": "#39d353", "r": "#e05252", "y": "#f2c94c",
    }, grid=[
        "..kkk.....................kkkkkkkk.........",
        "..krk.....................kjBBBBBk.........",
        "..kyk.....................kBBBBBBk.........",
        "..kvk.....................kkkkkkkk.........",
        "..kGk.......................kcck...........",
        "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk........",
        "kwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwWk........",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWk........",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWk........",
        "kWWWWWWWWWWgWWWWWWWWWWWgWWWWWWWWWWk........",
        "kccccccccccccccccccccccccccccccccxx........",
        "kccccccccccccccccccccccccccccccccxxkkkkkkkk",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWkWWWWWWWk",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWkkkkkkkkk",
        "kgggggggggggggggggggggggggggggggggk........",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWk........",
        "kwwwGGGGwwwgwwwwGGGGwwwgwwwGGGGwwWk........",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWk........",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWk........",
        "kwwwwwwwwwwgwwwwwwwwwwwgwwwwwwwwwWk........",
        "kWWWWWWWWWWgWWWWWWWWWWWgWWWWWWWWWWk........",
        "kccccccccccccccccccccccccccccccccck........",
        "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk........",
        ".kkk...........................kkk.........",
    ]),
}

# 確認用の床（office.py PAL の carpetA / carpetB）と休憩室の並び（drawLounge の x・休憩室の左端から）
FLOORS = {"light": ("#dccfb5", "#d0c2a5"), "dark": ("#3d3330", "#4a3f3a")}
LAYOUT = {"sofa": 24, "table": 156, "plant": 222, "coffee": 270, "vending": 410, "printer": 480}


def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def check(name, spec):
    g, pal, (bw, bh) = spec["grid"], {**OUTLINE, **spec["pal"]}, spec["box"]
    errs = [f"{name}: 行 {i} の幅 {len(r)}（1 行目は {len(g[0])}）" for i, r in enumerate(g) if len(r) != len(g[0])]
    errs += [f"{name}: 行 {i} に未定義の色 {ch!r}" for i, r in enumerate(g) for ch in set(r) if ch != "." and ch not in pal]
    if len(g[0]) * DOT + 2 > bw or len(g) * DOT + 1 > bh:  # 白フチ 1px の分は箱に残す
        errs.append(f"{name}: {len(g[0])}×{len(g)} ドットは箱 {bw}×{bh} に入らない")
    return errs


def render(spec):
    g, pal, (bw, bh) = spec["grid"], {**OUTLINE, **spec["pal"]}, spec["box"]
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ox, oy = (bw - len(g[0]) * DOT) // 2, bh - len(g) * DOT
    d = ImageDraw.Draw(img)
    for r, row in enumerate(g):
        for c, ch in enumerate(row):
            if ch != ".":
                x, y = ox + c * DOT, oy + r * DOT
                d.rectangle([x, y, x + DOT - 1, y + DOT - 1], fill=_rgb(pal[ch]) + (255,))
    a = img.getchannel("A").point(lambda v: 255 if v else 0)
    edge = ImageChops.subtract(a.filter(ImageFilter.MaxFilter(3)), a)
    out = Image.new("RGBA", img.size, RIM[:3] + (0,))
    out.putalpha(edge.point(lambda v: RIM[3] if v else 0))
    out.alpha_composite(img)
    return out


def carpet(w, h, theme):
    a, b = (_rgb(c) for c in FLOORS[theme])
    img = Image.new("RGBA", (w, h), a + (255,))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 16):
        for x in range(0, w, 16):
            d.rectangle([x + 2, y + 2, x + 5, y + 5], fill=b)
            d.rectangle([x + 10, y + 10, x + 13, y + 13], fill=b)
    return img


def preview(imgs):
    """休憩室の床側と同じ並び＋コード側が重ねる湯気・用紙（流れる途中）＋休憩中の人間（大きさの比較用）。"""
    human = next((p for p in [SPRITE_DIR / "human.png", *sorted(SPRITE_DIR.glob("*/human.png"))] if p.exists()), None)
    rows = []
    for theme in FLOORS:
        img = carpet(700, 140, theme)
        fy = 120
        for n, x in LAYOUT.items():
            img.alpha_composite(imgs[n], (x, fy - imgs[n].height))
        small = imgs["plant"].resize((round(36 * 0.7), round(70 * 0.7)), Image.NEAREST)  # 島の角
        img.alpha_composite(small, (668, fy - small.height))
        d = ImageDraw.Draw(img)
        cx, cy = LAYOUT["coffee"], fy - 80
        for sx in (38, 44):
            d.rectangle([cx + sx, cy + 32, cx + sx + 2, cy + 36], fill=(221, 221, 221, 255))
        px, py = LAYOUT["printer"], fy - 100
        d.rectangle([px + 140, py + 46, px + 163, py + 51], fill=(255, 255, 255, 255))
        if human:
            img.alpha_composite(Image.open(human).convert("RGBA"), (cx + 96, fy - 48))
        rows.append(img)
    sheet = Image.new("RGBA", (700, 140 * len(rows)))
    for i, r in enumerate(rows):
        sheet.alpha_composite(r, (0, i * 140))
    return sheet.resize((sheet.width * 2, sheet.height * 2), Image.NEAREST)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="assets/props/ 直下にも置く")
    args = ap.parse_args()
    errs = [e for n, s in PROPS.items() for e in check(n, s)]
    if errs:
        raise SystemExit("\n".join(errs))
    imgs = {n: render(s) for n, s in PROPS.items()}
    OUT_DIR.mkdir(exist_ok=True)
    for n, im in imgs.items():
        im.save(OUT_DIR / f"{n}.png")
        if args.install:
            im.save(INSTALL_DIR / f"{n}.png")
    preview(imgs).save(OUT_DIR / "preview.png")
    print(f"{len(imgs)} 点 → {OUT_DIR}" + (f" ＋ {INSTALL_DIR}" if args.install else ""))


if __name__ == "__main__":
    main()
