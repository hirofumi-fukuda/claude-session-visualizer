"""忍者チーム（NARUTO風）。人間＝火影、エージェント＝別々の忍。
AGENTS のキー＝描いた時のモデル（服の色）。書き出し名への割り当ては ASSIGN（入れ替え済み）。席の役目の目印は KINDS。"""
NAME = "ninja"
ORDER = 20
TITLE = "忍者チーム（NARUTO風）"
NOTE = "人間=火影／Opus=ナルト風・Sonnet=サクラ風・Haiku=参謀(シカマル風)・Fable=カカシ風(グレー)／不明=サスケ風に乗算"

PAL = {
    "M": "#c9d1dd",  # 額当ての金属
    "n": "#2c3350",  # 紺（布・ズボン・マスク）
    "Y": "#ffe45c",  # 金髪
    "P": "#f9b4d2",  # 桜色の髪
    "R": "#9e2f2f",  # 幟の影（KINDS hub）
    "c": "#f4e9c8",  # 巻物の紙（KINDS scribe）
    "C": "#d8c79a",  # 同・影
}

# 書き出し名 → 絵（c3eea27・10/15 の差し替えの並び）。agent-<キー>.png ＝ 値のモデルの絵をそのモデルの色で。
# 値の '*'＝FALLBACK のグレー版／キーの '*'＝agent.png。無いテーマはキーと同じモデル
ASSIGN = {"opus": "haiku", "sonnet": "fable", "haiku": "opus", "fable": "*", "*": "sonnet"}

# 種類＝席の役目の目印（config の office.sprite_variants で席に割り当てる）。ASSIGN 後の各絵に (x, y, 層) を重ねる。'.'＝透明。
# 人間は席の左に立つので、目印は右か横長に置く
KINDS = {
    "hub": ("司令塔（幟）", [(9, 0, [
        "rrB",
        "rwB",
        "rrB",
        "RRB",
        "..B",
        "..B",
        "..B",
        "..B",
        "..B",
        "..B",
        "..B",
        "..B",
    ])]),
    "scribe": ("記録係（巻物）", [(1, 10, [
        "BccccccccB",
        "scGcGGcGcs",
        "BCCCCCCCCB",
    ])]),
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
