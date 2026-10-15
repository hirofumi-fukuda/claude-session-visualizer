"""戦隊ヒーロー。エージェント＝スーツの色がそのままモデル色（パープル・ブルー・イエロー・ピンク）。人間＝司令官 or グリーン。"""
NAME = "sentai"
ORDER = 40
TITLE = "戦隊ヒーロー"
NOTE = "スーツ色＝モデル色（1枚を塗り分け）／人間=司令官（素顔）・候補2=追加戦士グリーン"

PAL = {
    "V": "#1e2233",  # バイザー
    "M": "#cfd6e2",  # マスクの口元（銀）
}

HUMAN = [
    ("司令官", [
        "...ffffff...",
        "..fffyyfff..",
        "..kkkkkkkk..",
        "..kssssssk..",
        "..ssessess..",
        "..ssssssss..",
        "...ssssss...",
        "..fffwwfff..",
        ".yfffwwfffy.",
        ".f.ffrrff.F.",
        ".f.fyffffF..",
        ".w.ffffff.w.",
        "...kkyykk...",
        "...KK..KK...",
        "...KK..KK...",
        "..kkk..kkk..",
    ]),
    ("追加戦士グリーン", [
        "...ffffff...",
        "..ffyyyyff..",
        "..ffffffff..",
        "..fWVVVVVf..",
        "..fVVVVVVf..",
        "..ffVVVVff..",
        "..fMMMMMMf..",
        "...FFFFFF...",
        ".yyffwwffyy.",
        ".yfffwwfffy.",
        ".f.ffwwff.F.",
        ".w.ffffff.w.",
        "...wwyyww...",
        "...ff..fF...",
        "...ff..fF...",
        "..www..www..",
    ]),
]

AGENTS = {
    "*": ("レンジャー", [
        "...311112...",
        "..31111112..",
        "..31111112..",
        "..3WVVVVV2..",
        "..3VVVVVV2..",
        "..31VVVV12..",
        "..31MMMM12..",
        "...222222...",
        "..3w1111w2..",
        ".311w11w112.",
        ".1.11ww12.2.",
        ".w.111112.w.",
        "...wwyyww...",
        "...11..12...",
        "...11..12...",
        "..www..www..",
    ]),
}
