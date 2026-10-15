"""ロボ（いまのコード内ピクセル絵の清書版）。人間は白衣の博士。"""
NAME = "robot"
ORDER = 10
TITLE = "ロボ（現行の清書）"
NOTE = "1枚をモデル色で塗り分け。頭の形で人間と見分ける"

PAL = {
    "W": "#e3e8ef",  # 金属・明
    "M": "#aeb7c6",  # 金属・中
    "D": "#7d879a",  # 金属・暗
    "v": "#1b2f3c",  # バイザー
    "V": "#5ad1e6",  # 光る目
}

HUMAN = [
    ("博士（白衣）", [
        "...kkkkkk...",
        "..kkkkkkkk..",
        "..kkkkkkkk..",
        "..kssssssk..",
        "..sGwGGwGs..",
        "..spssssps..",
        "...ssssss...",
        "..wwffffww..",
        ".wwwffffwwW.",
        ".w.wffffW.W.",
        ".s.wffffW.s.",
        "...wwwwwW...",
        "...KKKKKK...",
        "...KK..KK...",
        "...KK..KK...",
        "...kk..kk...",
    ]),
]

AGENTS = {
    "*": ("ロボ", [
        ".....33.....",
        ".....GG.....",
        "..WWWWWWWM..",
        ".WWWWWWWWWM.",
        ".WvvvvvvvvM.",
        "GWvVVvvVVvMG",
        ".WvvvvvvvvM.",
        "..MMMMMMMD..",
        "..31111112..",
        ".3111VV1112.",
        ".M.111112.M.",
        ".k.111112.k.",
        "...222222...",
        "...MD..MD...",
        "...MD..MD...",
        "..kkk..kkk..",
    ]),
}
