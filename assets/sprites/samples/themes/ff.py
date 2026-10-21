"""FF職業風のパーティ。人間＝王様（クエストを出す側）、エージェント＝モデルごとのジョブ。服の色＝モデル色。"""
NAME = "ff"
ORDER = 45
TITLE = "ジョブ（FF職業風）"
NOTE = "人間=王様／Opus=黒魔道士・Sonnet=ナイト・Haiku=シーフ・Fable=白魔道士／不明=赤魔道士"

PAL = {
    "b": "#c9a25e",  # とんがり帽子（麦わら色）
    "B": "#94733a",  # 同・影
    "h": "#7a4a24",  # 茶髪
    "V": "#1e1b26",  # 黒魔の顔の闇
    "Y": "#ffe45c",  # 光る目
    "M": "#cfd6e2",  # 鎧の銀
    "N": "#8d97a8",  # 同・影
}

HUMAN = [
    ("王様", [
        "...y.yy.y...",
        "...yyrryy...",
        "..WWWWWWWW..",
        "..WssssssW..",
        "..WsessesW..",
        "..WsWWWWsW..",
        "...WWWWWW...",
        ".rfffWWfffr.",
        "rrfffffffFrr",
        "rrfwfffffwrr",
        "rrswffffwsrr",
        "rrffyyyyffrr",
        "rrffffffffrr",
        ".rffffffffr.",
        "..ffffffFF..",
        "...kk..kk...",
    ]),
]

AGENTS = {
    "opus": ("黒魔道士", [
        ".......b....",
        "......bb....",
        ".....bbbB...",
        "....bbbbB...",
        "...bbbbbbB..",
        "bbbbbbbbbbBB",
        "...VYVVYV...",
        "..31111112..",
        ".3111111112.",
        ".3111111112.",
        ".k31111112k.",
        "..31111112..",
        "..31111112..",
        "..31111112..",
        "..22222222..",
        "...kk..kk...",
    ]),
    "sonnet": ("ナイト", [
        ".....rr.....",
        "....MMMM....",
        "...MMMMMN...",
        "..MMMMMMMN..",
        "..MeeeeeeN..",
        "..MMMMMMMN..",
        "...MMMMMN...",
        "..31111112..",
        ".N31111112N.",
        ".M311yy112M.",
        ".M.111112.N.",
        ".N.111112.N.",
        "...222222...",
        "...MN..MN...",
        "...MN..MN...",
        "..NNN..NNN..",
    ]),
    "haiku": ("シーフ", [
        "....3112....",
        "..31111112..",
        ".31111111122",
        "..hhhhhhhh.2",
        "..hsessesh..",
        "..GGGGGGGG..",
        "...GGGGGG...",
        "..31111112..",
        ".s31111112s.",
        ".s.111112.s.",
        ".s.hhyhhh.s.",
        "...111112.k.",
        "...KKKKKK.W.",
        "...KK..KK.W.",
        "...hh..hh...",
        "..hhh..hhh..",
    ]),
    "fable": ("白魔道士", [
        "....wwww....",
        "...wwwwwW...",
        "..wwwwwwwW..",
        "..wssssssW..",
        "..wsessesW..",
        "..wpsssspW..",
        "..wwssssWW..",
        "..ww1111WW..",
        ".1wwwwwwWW1.",
        ".1wwwwwwWW1.",
        ".s.wwwwWW.s.",
        "..wwwwwwWW..",
        "..wwwwwwWW..",
        "..1w11w11W..",
        "..11111111..",
        "...kk..kk...",
    ]),
}

FALLBACK = ("赤魔道士", [
    "........ww..",
    ".......ww...",
    "....bbbwb...",
    "...bbbbbbB..",
    "..bbbbbbbbB.",
    "bbbbbbbbbbBB",
    "...VYVVYV...",
    "..31111112..",
    ".3111111112.",
    ".3111111112.",
    ".k31111112k.",
    "..31111112..",
    "..31111112..",
    "..31111112..",
    "..22222222..",
    "...kk..kk...",
])

# ---------- 種類（競馬場ページの席カードで選ぶ・agent-<種類>-<モデル>[-<ポーズ>].png・10/18 デザイナー）----------
# 上の 4 体を、どのモデルの席でも選べるようにする（モデル色の塗り分けはそのまま）
# 1枚＝BODY（空）＋種類の層（絵まるごと・服や体はモデル色の 1/2/3）＋ポーズの手前の層。モデル別の小物は足さない（色で見分ける）
BODY = ["." * 12] * 16

SPECIES = {
    "kuro": ("黒魔道士", {}, AGENTS["opus"][1], {}),
    "knight": ("ナイト", {}, AGENTS["sonnet"][1], {}),
    "thief": ("シーフ", {}, AGENTS["haiku"][1], {}),
    "shiro": ("白魔道士", {}, AGENTS["fable"][1], {}),
}

ACCESSORIES = {m: ("—", []) for m in ("opus", "sonnet", "haiku", "fable")}

# ポーズ（idle＝上の絵そのもの）。種類の層が絵まるごとなので体は変えず、手前の層だけ重ねる
POSES = {
    # 作業中: ノート PC に向かう（ふたを後ろから見た形・足元を隠す）
    "typing": ("ノート PC", BODY, [("laptop", [
        ".GGGGGGGG.",
        ".GGGwwGGG.",
        ".GGGGGGGG.",
        "kkkkkkkkkk",
    ])]),
    # 許可待ち: 右上に黄色い「!」
    "wait": ("許可待ち（!）", BODY, [("bang", [
        "y",
        "y",
        ".",
        "y",
    ])]),
    # 離席: うとうと＝鼻ちょうちん
    "rest": ("うとうと", BODY, [("bubble", [
        "wW",
        "WW",
    ])]),
    # 影分身（サブエージェント）: 絵はそのまま＋足元に煙（ボンッ）。ノート PC は持たない＝煙が分身の目印。
    # office.py が 0.67 倍・不透明度 0.8 で主の机に並べる（10/21 S25 決定・デザイナー案 c 弱）
    "clone": ("影分身（足元の煙）", BODY, [
        ("smoke", [
            ".ww......ww.",
            "wwWww..wwWww",
            ".gWWWggWWWg.",
        ]),
    ]),
}
ANCHORS = {"laptop": (1, 12), "bang": (11, 0), "bubble": (10, 5), "smoke": (0, 13)}
