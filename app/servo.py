"""サーボモーター制御（将来の拡張用ヘルパー）。

ハードウェアPWM (BCM12=物理32ピン / BCM13=物理33ピン) を使う。
事前に /boot/firmware/config.txt へ以下を追記して再起動すること:

    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4

使い方:
    from servo import Servo
    s = Servo(channel=0)   # channel 0 = BCM12, 1 = BCM13
    s.angle(90)            # 0〜180度
    s.close()
"""
import os
import time

PWMCHIP = "/sys/class/pwm/pwmchip0"
PERIOD_NS = 20_000_000          # 20ms (50Hz)
MIN_NS = 500_000                # 0度 (SG90: 0.5ms)
MAX_NS = 2_500_000              # 180度 (SG90: 2.5ms)


class Servo:
    def __init__(self, channel=0):
        self.path = f"{PWMCHIP}/pwm{channel}"
        if not os.path.isdir(PWMCHIP):
            raise RuntimeError(
                "pwmchip0 がありません。config.txt に dtoverlay=pwm-2chan を追加して再起動してください。")
        if not os.path.isdir(self.path):
            self._write(f"{PWMCHIP}/export", str(channel))
            time.sleep(0.1)
        self._write(f"{self.path}/period", str(PERIOD_NS))
        self._write(f"{self.path}/enable", "1")

    def _write(self, path, value):
        with open(path, "w") as f:
            f.write(value)

    def angle(self, deg):
        """0〜180度に動かす。"""
        deg = max(0.0, min(180.0, float(deg)))
        duty = int(MIN_NS + (MAX_NS - MIN_NS) * deg / 180.0)
        self._write(f"{self.path}/duty_cycle", str(duty))

    def release(self):
        """PWM を止めてプルプル音を消す（位置保持もやめる）。"""
        self._write(f"{self.path}/enable", "0")

    def close(self):
        try:
            self.release()
        except OSError:
            pass


if __name__ == "__main__":
    s = Servo(0)
    for a in (0, 90, 180, 90):
        print(f"angle {a}")
        s.angle(a)
        time.sleep(0.8)
    s.close()
