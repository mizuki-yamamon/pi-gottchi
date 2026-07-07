#!/usr/bin/env python3
"""起動スプラッシュ v2: シンプルな「もこ じゅんびちゅう…」画面。

- 起動中はドットが流れる（boot_XX.raw）
- 起動猶予を過ぎてもWiFiが繋がらない場合は「WiFiにつながらないよ」の顔に切替
  （繋がればドットに戻る。もこ本体はオフラインでも起動するので情報表示のみ）
- もこ本体(chara.py)が起動すると SIGTERM され、画面を引き継ぐ

PIL/numpy不使用。事前生成のRGB565生フレームをblitするだけの軽量スクリプト。
"""
import glob
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, "/home/mizukichi/Whisplay/runtime")
HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(HERE, "boot_frames")
FRAME_SEC = 0.45
WIFI_GRACE = 40.0        # 起動からこの秒数はWiFi未接続でもドット表示のまま
WIFI_CHECK_SEC = 5.0

_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True


def load(prefix):
    return [open(p, "rb").read()
            for p in sorted(glob.glob(os.path.join(FRAMES_DIR, prefix + "*.raw")))]


def wifi_up():
    """wlan0 がIPv4アドレスを持っていれば True。"""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "wlan0"],
                             capture_output=True, text=True, timeout=5).stdout
        return " inet " in out or "inet " in out
    except Exception:
        return False


def wait_board(timeout=40):
    from whisplay import WhisplayBoard
    deadline = time.time() + timeout
    while True:
        try:
            return WhisplayBoard()
        except Exception:
            if time.time() > deadline or _stop:
                raise
            time.sleep(0.5)


def main():
    signal.signal(signal.SIGTERM, _on_term)
    boot = load("boot_")
    err = load("wifi_err_")
    if not boot:
        return
    board = wait_board()
    t0 = time.time()
    last_check, net_ok = 0.0, True
    i = 0
    try:
        board.set_backlight(100)
        board.set_rgb(80, 40, 50)
        while not _stop:
            now = time.time()
            if now - last_check >= WIFI_CHECK_SEC:
                last_check = now
                net_ok = wifi_up()
            show_err = err and not net_ok and (now - t0) > WIFI_GRACE
            frames = err if show_err else boot
            board.set_rgb(*((200, 60, 40) if show_err else (80, 40, 50)))
            board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT,
                             frames[i % len(frames)])
            i += 1
            time.sleep(FRAME_SEC)
    finally:
        board.cleanup()


if __name__ == "__main__":
    main()
