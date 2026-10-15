"""忍者チーム（NARUTO風）。人間＝火影、エージェント＝モデルごとに別の忍。服の色＝モデル色。"""
NAME = "ninja"
ORDER = 20
TITLE = "忍者チーム（NARUTO風）"
NOTE = "人間=火影／Opus=参謀(シカマル風)・Sonnet=サスケ風・Haiku=ナルト風・Fable=サクラ風／不明=カカシ風"

PAL = {
    "M": "#c9d1dd",  # 額当ての金属
    "n": "#2c3350",  # 紺（布・ズボン・マスク）
    "Y": "#ffe45c",  # 金髪
    "P": "#f9b4d2",  # 桜色の髪
}

HUMAN = [
    ("火影", [
        "....wwww....",
        "...wwrrww...",
        "..wwrrrrww..",
        "wwwwwwwwwwww",
        "..kssssssk..",
        "..ksessesk..",
        "...ssssss...",
        "..wwffffww..",
        ".wwwffffwww.",
        ".w.wffffw.w.",
        ".s.wffffw.s.",
        "..wwwwwwww..",
        "..rrwrrwrr..",
        "...nn..nn...",
        "...nn..nn...",
        "...kk..kk...",
    ]),
]

AGENTS = {
    "opus": ("参謀（シカマル風）", [
        "...k.kk.k...",
        "....kkkk....",
        "...kkkkkk...",
        "..kkkkkkkk..",
        "..kssssssk..",
        "..seessees..",
        "..ssssssss..",
        "...ssssss...",
        "..31111112..",
        ".G31111112M.",
        ".G.111112.n.",
        ".s.111112.s.",
        "...nnnnnn...",
        "...nn..nn...",
        "...WW..WW...",
        "...kk..kk...",
    ]),
    "sonnet": ("サスケ風", [
        "......k.kk..",
        "...kkkkkkkk.",
        "..kkkkkkkkkk",
        "..nMMMMMMnk.",
        "..kssssssk..",
        "..ksessesk..",
        "..kssssssk..",
        "...ssssss...",
        "..31111112..",
        ".3111111112.",
        ".W.111112.W.",
        ".s.111112.s.",
        "...wwwwww...",
        "...wW..wW...",
        "...ss..ss...",
        "...nn..nn...",
    ]),
    "haiku": ("ナルト風", [
        "..Y.Y.YY.Y..",
        ".YYYYYYYYYY.",
        ".YYYYYYYYYY.",
        ".YnMMMMMMnY.",
        ".YYssssssYY.",
        "..ssessess..",
        "..sesssses..",
        "...ssssss...",
        "..kkk11kkk..",
        ".k31111112k.",
        ".1.111112.2.",
        ".s.111112.s.",
        "...111112...",
        "...11..12...",
        "...11..12...",
        "...nn..nn...",
    ]),
    "fable": ("サクラ風", [
        "...PPPPPP...",
        "..nnMMMMnn..",
        "..PPPPPPPP..",
        ".PPssssssPP.",
        ".PPsessesPP.",
        ".PPpsssspPP.",
        ".PPPssssPPP.",
        ".P31111112P.",
        ".s31111112s.",
        ".s.111112.s.",
        ".s.111112.s.",
        "...111112...",
        "...nnnnnn...",
        "...ss..ss...",
        "...ss..ss...",
        "...nn..nn...",
    ]),
}

FALLBACK = ("上忍（カカシ風）", [
    ".......WW...",
    "....WWWWWW..",
    "...WWWWWWWW.",
    "..nMMMMMMnW.",
    "..WsesnnnW..",
    "..WnnnnnnW..",
    "...nnnnnn...",
    "..31111112..",
    ".n31111112n.",
    ".n.111112.n.",
    ".k.111112.k.",
    "...nnnnnn...",
    "...nn..nn...",
    "...nn..nn...",
    "...WW..WW...",
    "...kk..kk...",
])
