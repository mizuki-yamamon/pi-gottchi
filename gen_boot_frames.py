#!/usr/bin/env python3
"""起動スプラッシュ用フレーム生成 v2（ローカル実行）。

- boot_XX.raw : シンプル起動画面（「もこ」+ 点滅ドット3種）
- wifi_err_X.raw : WiFi未接続の顔（sad顔 + WiFi×アイコン、2枚で点滅）
RGB565生バイト列(240x280x2)。実機 splash.py はこれをblitするだけ。
"""
import glob
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
import face  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "boot_frames")
W, H = face.W, face.H
INK = (110, 88, 80)
SUB = (170, 150, 142)

FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def load_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def rgb565(img):
    px = img.convert("RGB").load()
    buf = bytearray(W * H * 2)
    i = 0
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            struct.pack_into(">H", buf, i,
                             ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
            i += 2
    return bytes(buf)


def save(img, name):
    with open(os.path.join(OUT, f"{name}.raw"), "wb") as f:
        f.write(rgb565(img))
    img.save(os.path.join(OUT, f"{name}.png"))


def center_text(draw, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def boot_frames():
    big = load_font(44)
    small = load_font(17)
    for n in range(3):
        img = face._background("day")
        draw = ImageDraw.Draw(img)
        # 小さな寝顔（丸+閉じ目）でブランド感
        cx, cy = W // 2, 92
        draw.ellipse([cx - 40, cy - 36, cx + 40, cy + 36],
                     fill=(255, 252, 244), outline=(214, 178, 152), width=3)
        for sx in (-1, 1):
            ex = cx + sx * 16
            draw.arc([ex - 8, cy - 8, ex + 8, cy + 4], 0, 180, fill=(74, 56, 50), width=3)
        draw.arc([cx - 5, cy + 8, cx + 5, cy + 16], 0, 180, fill=(198, 96, 96), width=3)
        center_text(draw, 150, "もこ", big, INK)
        center_text(draw, 212, "じゅんびちゅう", small, SUB)
        for i in range(3):  # ドット
            x = W // 2 - 26 + i * 26
            on = i == n
            r = 6 if on else 4
            col = (214, 150, 160) if on else (228, 200, 202)
            draw.ellipse([x - r, 246 - r, x + r, 246 + r], fill=col)
        save(img, f"boot_{n:02d}")


def wifi_icon(draw, cx, cy, crossed):
    """WiFi扇アイコン（crossed=Trueで×付き）。"""
    for r in (26, 17, 8):
        draw.arc([cx - r, cy - r, cx + r, cy + r], 225, 315,
                 fill=(120, 140, 180), width=4)
    draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(120, 140, 180))
    if crossed:
        draw.line([cx - 24, cy - 26, cx + 24, cy + 6], fill=(225, 90, 90), width=5)


def wifi_error_frames():
    small = load_font(17)
    for n in range(2):
        img = face.render("sad", frame=3, extras={"blink": n == 1})
        draw = ImageDraw.Draw(img)
        # 上部にWiFi×アイコンと文言
        draw.rounded_rectangle([28, 8, W - 28, 66], radius=12,
                               fill=(255, 255, 255), outline=(226, 200, 196), width=2)
        wifi_icon(draw, 62, 44, crossed=(n == 0))
        draw.text((92, 22), "WiFiに", font=small, fill=INK)
        draw.text((92, 42), "つながらないよ", font=small, fill=INK)
        save(img, f"wifi_err_{n}")


def main():
    os.makedirs(OUT, exist_ok=True)
    for old in glob.glob(os.path.join(OUT, "egg_*")):
        os.remove(old)
    boot_frames()
    wifi_error_frames()
    print("frames:", sorted(os.path.basename(p)
                            for p in glob.glob(os.path.join(OUT, "*.raw"))))


if __name__ == "__main__":
    main()
