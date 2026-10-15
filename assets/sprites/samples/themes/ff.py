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
