"""「もこ」の顔描画モジュール。

実機 LCD (240x280) とローカルプレビューで共用する純粋描画コード。
render(expr, frame, extras) が PIL Image を返す。ハード依存なし。
"""
import math

from PIL import Image, ImageDraw

W, H = 240, 280

# ---------- 色 ----------
INK = (74, 56, 50)              # 目・輪郭の焦げ茶
MOUTH_LINE = (198, 96, 96)
MOUTH_FILL = (150, 74, 70)
TONGUE = (255, 160, 160)
CHEEK = (255, 186, 196)
HEART = (255, 128, 150)
SWEAT = (140, 190, 245)
NOTE = (120, 150, 235)

BODY = {"day": (255, 252, 244), "night": (198, 198, 216)}
EDGE = {"day": (214, 178, 152), "night": (138, 138, 164)}

# 背景グラデーション (上端色, 下端色)
PALETTES = {
    "day": ((255, 246, 235), (255, 223, 214)),
    "night": ((45, 49, 82), (21, 23, 45)),
    "listen": ((222, 239, 255), (193, 220, 250)),
    "think": ((237, 231, 252), (214, 206, 245)),
}

_STARS = [(28, 34), (66, 20), (118, 44), (170, 26), (208, 58),
          (40, 84), (150, 72), (200, 108), (24, 130), (96, 30)]

_bg_cache = {}


def _palette_key(expr):
    if expr == "sleeping":
        return "night"
    if expr == "listening":
        return "listen"
    if expr == "thinking":
        return "think"
    return "day"


def _background(key):
    """縦グラデーション背景（式ごとに1度だけ生成してキャッシュ）。"""
    if key not in _bg_cache:
        top, bottom = PALETTES[key]
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        for y in range(H):
            t = y / (H - 1)
            col = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
            draw.line([0, y, W, y], fill=col)
        if key == "night":
            for x, y in _STARS:
                draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(216, 220, 255))
        _bg_cache[key] = img
    return _bg_cache[key].copy()


# ---------- パーツ ----------
def _heart(draw, x, y, r, fill):
    draw.ellipse([x - r, y - r, x, y], fill=fill)
    draw.ellipse([x, y - r, x + r, y], fill=fill)
    draw.polygon([(x - r, y - r * 0.35), (x + r, y - r * 0.35), (x, y + r)], fill=fill)


def _sparkle(draw, x, y, r, fill):
    k = r * 0.28
    draw.polygon([(x, y - r), (x + k, y - k), (x + r, y), (x + k, y + k),
                  (x, y + r), (x - k, y + k), (x - r, y), (x - k, y - k)], fill=fill)


def _note(draw, x, y, fill):
    draw.ellipse([x - 6, y + 8, x + 4, y + 16], fill=fill)
    draw.line([x + 3, y - 12, x + 3, y + 12], fill=fill, width=3)
    draw.line([x + 3, y - 12, x + 13, y - 7], fill=fill, width=3)


def _zees(draw, x, y, phase):
    """寝息の Z を線で描く（フォント非依存）。"""
    for i, size in enumerate((8, 11, 14)):
        if phase < i * 0.33:
            continue
        zx = x + i * 15
        zy = y - i * 18
        s = size
        col = (232, 236, 255)
        draw.line([zx - s, zy - s, zx + s, zy - s], fill=col, width=3)
        draw.line([zx + s, zy - s, zx - s, zy + s], fill=col, width=3)
        draw.line([zx - s, zy + s, zx + s, zy + s], fill=col, width=3)


def _sweat(draw, x, y):
    draw.polygon([(x, y - 9), (x - 6, y + 4), (x + 6, y + 4)], fill=SWEAT)
    draw.ellipse([x - 7, y - 2, x + 7, y + 10], fill=SWEAT)


def _ear_icon(draw, x, y, frame):
    """待機中の「聞いてるよ」サイン（耳+波紋がふわふわ動く）。"""
    col = (150, 175, 220)
    draw.ellipse([x - 7, y - 10, x + 7, y + 10], outline=col, width=3)
    draw.ellipse([x - 3, y + 2, x + 3, y + 8], fill=col)
    k = (frame // 2) % 3
    for i in range(k + 1):
        r = 12 + i * 6
        draw.arc([x - r, y - r, x + r, y + r], -40, 40, fill=col, width=2)


def _battery_icon(draw, x, y, pct, frame):
    """低残量時に表示する電池アイコン（10%以下は点滅）。"""
    w, h = 34, 16
    draw.rounded_rectangle([x, y, x + w, y + h], radius=3, outline=INK, width=3)
    draw.rectangle([x + w + 2, y + 5, x + w + 5, y + h - 5], fill=INK)
    if pct > 10 or frame % 6 < 3:
        fw = max(2, int((w - 8) * pct / 100))
        col = ((120, 205, 130) if pct > 40 else
               (255, 175, 70) if pct > 20 else (238, 92, 92))
        draw.rectangle([x + 4, y + 4, x + 4 + fw, y + h - 4], fill=col)


# ---------- 目 ----------
def _eye_open(draw, ex, ey, scale=1.0, look=(0, 0)):
    rx, ry = 13 * scale, 17 * scale
    dx, dy = look[0] * 4, look[1] * 3
    draw.ellipse([ex - rx + dx, ey - ry + dy, ex + rx + dx, ey + ry + dy], fill=INK)
    hr = 5 * scale
    draw.ellipse([ex + dx + 2, ey + dy - ry * 0.65, ex + dx + 2 + hr * 2,
                  ey + dy - ry * 0.65 + hr * 2], fill=(255, 255, 255))
    sr = 2.4 * scale
    draw.ellipse([ex + dx - 6 - sr, ey + dy + 6 - sr, ex + dx - 6 + sr,
                  ey + dy + 6 + sr], fill=(255, 255, 255))


def _eye_happy(draw, ex, ey):
    draw.arc([ex - 13, ey - 9, ex + 13, ey + 13], 180, 360, fill=INK, width=5)


def _eye_closed(draw, ex, ey):
    draw.arc([ex - 12, ey - 12, ex + 12, ey + 8], 0, 180, fill=INK, width=5)


def _eye_dizzy(draw, ex, ey, frame):
    a0 = (frame * 40) % 360
    for r in (13, 9, 5):
        draw.arc([ex - r, ey - r, ex + r, ey + r], a0 + r * 30,
                 a0 + r * 30 + 260, fill=INK, width=3)


def _eye_sparkle(draw, ex, ey):
    draw.ellipse([ex - 13, ey - 17, ex + 13, ey + 17], fill=INK)
    _sparkle(draw, ex + 1, ey - 3, 8, (255, 255, 255))
    draw.ellipse([ex - 7, ey + 8, ex - 3, ey + 12], fill=(255, 255, 255))


def _eye_surprised(draw, ex, ey):
    draw.ellipse([ex - 13, ey - 15, ex + 13, ey + 15], outline=INK, width=4)
    draw.ellipse([ex - 5, ey - 5, ex + 5, ey + 5], fill=INK)


def _eye_droopy(draw, ex, ey):
    """半目（おなかすいた・ぐったり）。"""
    draw.ellipse([ex - 12, ey - 6, ex + 12, ey + 14], fill=INK)
    draw.line([ex - 14, ey - 5, ex + 14, ey - 5], fill=INK, width=6)   # 重いまぶた
    draw.ellipse([ex + 1, ey - 1, ex + 7, ey + 5], fill=(255, 255, 255))


EYES = {
    "idle": lambda d, x, y, f, blink: _eye_closed(d, x, y) if blink else _eye_open(d, x, y),
    "happy": lambda d, x, y, f, blink: _eye_happy(d, x, y),
    "excited": lambda d, x, y, f, blink: _eye_sparkle(d, x, y),
    "surprised": lambda d, x, y, f, blink: _eye_surprised(d, x, y),
    "dizzy": lambda d, x, y, f, blink: _eye_dizzy(d, x, y, f),
    "listening": lambda d, x, y, f, blink: _eye_open(d, x, y, 1.12),
    "thinking": lambda d, x, y, f, blink: _eye_open(d, x, y, 1.0, look=(0.7, -0.7)),
    "talking": lambda d, x, y, f, blink: _eye_open(d, x, y) if not blink else _eye_closed(d, x, y),
    "sleeping": lambda d, x, y, f, blink: _eye_closed(d, x, y),
    "sad": lambda d, x, y, f, blink: _eye_open(d, x, y, 0.9, look=(0, 0.8)),
    "hungry": lambda d, x, y, f, blink: _eye_droopy(d, x, y),
}


# ---------- 口 ----------
def _mouth_omega(draw, cx, my):
    draw.arc([cx - 11, my - 5, cx + 1, my + 7], 0, 180, fill=MOUTH_LINE, width=4)
    draw.arc([cx - 1, my - 5, cx + 11, my + 7], 0, 180, fill=MOUTH_LINE, width=4)


def _mouth_smile(draw, cx, my):
    draw.arc([cx - 14, my - 10, cx + 14, my + 8], 0, 180, fill=MOUTH_LINE, width=5)


def _mouth_open(draw, cx, my, w=13, h=15):
    draw.ellipse([cx - w, my - h * 0.4, cx + w, my + h], fill=MOUTH_FILL)
    draw.ellipse([cx - w * 0.6, my + h * 0.3, cx + w * 0.6, my + h], fill=TONGUE)


def _mouth_o(draw, cx, my, r=6):
    draw.ellipse([cx - r, my - r, cx + r, my + r], outline=MOUTH_LINE, width=4)


def _mouth_wavy(draw, cx, my):
    pts = [(cx + i * 6 - 15, my + (4 if i % 2 else -2)) for i in range(6)]
    draw.line(pts, fill=MOUTH_LINE, width=4)


def _mouth_sad(draw, cx, my):
    draw.arc([cx - 12, my, cx + 12, my + 14], 180, 360, fill=MOUTH_LINE, width=4)


def _draw_mouth(draw, expr, cx, my, frame, mouth_open):
    if expr == "talking":
        if mouth_open:
            _mouth_open(draw, cx, my)
        else:
            _mouth_omega(draw, cx, my)
    elif expr in ("happy",):
        _mouth_smile(draw, cx, my)
    elif expr == "excited":
        _mouth_open(draw, cx, my, 14, 16)
    elif expr == "surprised":
        _mouth_o(draw, cx, my)
    elif expr == "dizzy":
        _mouth_wavy(draw, cx, my)
    elif expr == "sleeping":
        _mouth_o(draw, cx, my, 5 + (1 if frame % 12 < 6 else 0))
    elif expr == "sad":
        _mouth_sad(draw, cx, my)
    elif expr == "hungry":
        _mouth_wavy(draw, cx, my)
        draw.ellipse([cx + 15, my + 5, cx + 23, my + 15], fill=SWEAT)  # よだれ
    else:
        _mouth_omega(draw, cx, my)


# ---------- 本体 ----------
def _draw_body(draw, cx, base_by, k, night, shake_x=0):
    """ぷにぷにボディ。k はスクワッシュ量 (-3..3)。下端 base_by 固定。"""
    body, edge = BODY["night" if night else "day"], EDGE["night" if night else "day"]
    rx, ry = 90 + k, 86 - k
    cy = base_by - ry
    cx += shake_x

    # 接地の影
    draw.ellipse([cx - rx + 14, base_by - 8, cx + rx - 14, base_by + 14],
                 fill=(226, 196, 188) if not night else (16, 18, 36))
    # 耳（本体より先に描いて根元を隠す）
    for sx in (-1, 1):
        ex = cx + sx * (rx - 34)
        eyy = cy - ry + 14
        draw.ellipse([ex - 19, eyy - 19, ex + 19, eyy + 19], fill=body, outline=edge, width=3)
        draw.ellipse([ex - 9, eyy - 8, ex + 9, eyy + 10], fill=CHEEK if not night else (170, 160, 185))
    # あほ毛
    draw.arc([cx - 6, cy - ry - 22, cx + 26, cy - ry + 10], 150, 330, fill=edge, width=4)
    # 体
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=body, outline=edge, width=3)
    return cx, cy


def _draw_cheeks(draw, cx, cy, night):
    col = CHEEK if not night else (168, 158, 184)
    for sx in (-1, 1):
        x = cx + sx * 56
        draw.ellipse([x - 15, cy + 12, x + 15, cy + 30], fill=col)


def _draw_accessories(draw, expr, cx, cy, frame, extras):
    if expr == "sleeping":
        _zees(draw, cx + 56, cy - 92, (frame % 24) / 24)
    elif expr == "listening":
        bob = int(3 * math.sin(frame * 0.8))
        _note(draw, cx + 74, cy - 78 + bob, NOTE)
    elif expr == "thinking":
        for i in range(3):
            if (frame // 3) % 4 > i:
                x = cx + 46 + i * 18
                y = cy - 100 - i * 8
                draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(136, 124, 190))
    elif expr == "dizzy":
        _sweat(draw, cx + 68, cy - 62)
        for sx in (-1, 1):  # 揺れ線
            x = cx + sx * 104
            for dy in (-20, 0, 20):
                draw.arc([x - 6, cy + dy - 8, x + 6, cy + dy + 8],
                         90 if sx > 0 else 270, 270 if sx > 0 else 450,
                         fill=(228, 160, 150), width=3)
    elif expr == "surprised":
        for sx in (-1, 1):  # びっくり線
            x = cx + sx * 86
            draw.line([x, cy - 96, x + sx * 12, cy - 110], fill=INK, width=4)
            draw.line([x + sx * 14, cy - 80, x + sx * 30, cy - 88], fill=INK, width=4)
    # ふわふわハート（なでた時など）
    t = extras.get("hearts_t")
    if t is not None and 0.0 <= t <= 1.0:
        for i, sx in enumerate((-1, 0, 1)):
            ht = t - i * 0.12
            if ht < 0:
                continue
            x = cx + sx * 46 + int(6 * math.sin((frame + i * 3) * 0.7))
            y = cy - 92 - int(ht * 46)
            _heart(draw, x, y, max(4, int(9 * (1 - ht * 0.5))), HEART)


# ---------- メイン ----------
def render(expr, frame, extras=None):
    """1フレーム描画して PIL Image (240x280) を返す。"""
    extras = extras or {}
    night = expr == "sleeping"
    img = _background(_palette_key(expr))
    draw = ImageDraw.Draw(img)

    if expr == "sleeping":
        k = 1.5 * math.sin(frame * 0.25)          # 寝息はゆっくり
    elif expr in ("excited", "surprised", "dizzy"):
        k = 3.0 * math.sin(frame * 1.1)           # 興奮時は速く
    else:
        k = 2.2 * math.sin(frame * 0.55)
    shake_x = int(5 * math.sin(frame * 2.2)) if expr == "dizzy" else 0

    cx, cy = _draw_body(draw, W // 2, 238, k, night, shake_x)
    _draw_cheeks(draw, cx, cy, night)

    blink = extras.get("blink", False)
    ey = cy - 14
    eye_fn = EYES.get(expr, EYES["idle"])
    for sx in (-1, 1):
        eye_fn(draw, cx + sx * 36, ey, frame, blink)

    _draw_mouth(draw, expr, cx, cy + 34, frame, extras.get("mouth_open", False))
    _draw_accessories(draw, expr, cx, cy, frame, extras)
    if extras.get("battery") is not None and expr != "sleeping":
        _battery_icon(draw, W - 52, 10, extras["battery"], frame)
    if extras.get("mic_ready"):
        _ear_icon(draw, 24, 24, frame)
    return img


EXPRESSIONS = list(EYES.keys())
