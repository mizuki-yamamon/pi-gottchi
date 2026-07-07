#!/usr/bin/env python3
"""ローカルプレビュー: 全表情をグリッド1枚のPNGに描き出す（開発用）。"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
import face  # noqa: E402

COLS = 5
SCALE = 1
PAD = 8

EXTRAS = {
    "idle": {"bond": 3},
    "happy": {"bond": 3, "hearts_t": 0.4},
    "talking": {"bond": 3, "mouth_open": True},
}


def main():
    exprs = face.EXPRESSIONS
    rows = (len(exprs) + COLS - 1) // COLS
    cw, ch = face.W * SCALE + PAD, face.H * SCALE + PAD + 18
    sheet = Image.new("RGB", (COLS * cw + PAD, rows * ch + PAD), (40, 40, 46))
    d = ImageDraw.Draw(sheet)
    for i, expr in enumerate(exprs):
        img = face.render(expr, frame=5, extras=EXTRAS.get(expr, {"bond": 3}))
        x = PAD + (i % COLS) * cw
        y = PAD + (i // COLS) * ch
        sheet.paste(img, (x, y))
        d.text((x + 4, y + face.H + 3), expr, fill=(240, 240, 240))
    out = os.path.join(os.path.dirname(__file__), "preview.png")
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
