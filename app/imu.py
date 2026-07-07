"""加速度センサー（MPU6050系 / ADXL345 / H3LIS331DL）の自動検出と揺れ検知。

センサー未接続でも安全に動作し、あとから配線すれば自動で認識する。
PiSugar の RTC が 0x68 にいるため、MPU6050 は AD0=3.3V（アドレス 0x69）推奨。
0x68 も WHO_AM_I を確認してから使うので誤検出はしない。
H3LIS331DL (SparkFun SEN-14480) は ±100g の高衝撃用のため分解能が粗い
（約0.05g/LSB）。手揺れの「軽い揺れ」はやや検知しづらい点に注意。
"""
import threading
import time
from collections import deque

try:
    from smbus2 import SMBus
except ImportError:  # smbus2 未導入でもアプリ本体は動かす
    SMBus = None

I2C_BUS = 1
RESCAN_SEC = 10.0            # 未検出時に再スキャンする間隔
POLL_HZ = 40                 # 検出後のサンプリング周波数

# 揺れ判定: WINDOW 秒内に THRESH[g] を超えるピークが PEAKS 回で発火
LIGHT = {"thresh": 0.35, "peaks": 2, "cooldown": 1.6}
HARD = {"thresh": 1.00, "peaks": 3, "cooldown": 3.0}
WINDOW_SEC = 0.7

_MPU_WHO_AM_I_OK = {0x68, 0x69, 0x70, 0x71, 0x73, 0x75, 0x19}


class _MPU6050:
    """MPU6050 / 6500 / 9250 系。±2g、16384 LSB/g。"""

    def __init__(self, bus, addr):
        self.bus, self.addr = bus, addr
        bus.write_byte_data(addr, 0x6B, 0x00)      # PWR_MGMT_1: スリープ解除
        time.sleep(0.05)

    def read_g(self):
        d = self.bus.read_i2c_block_data(self.addr, 0x3B, 6)
        ax = _s16((d[0] << 8) | d[1]) / 16384.0
        ay = _s16((d[2] << 8) | d[3]) / 16384.0
        az = _s16((d[4] << 8) | d[5]) / 16384.0
        return ax, ay, az


class _ADXL345:
    """ADXL345。フル分解能 ±2g、約256 LSB/g。"""

    def __init__(self, bus, addr=0x53):
        self.bus, self.addr = bus, addr
        bus.write_byte_data(addr, 0x31, 0x08)      # DATA_FORMAT: full res
        bus.write_byte_data(addr, 0x2D, 0x08)      # POWER_CTL: measure
        time.sleep(0.05)

    def read_g(self):
        d = self.bus.read_i2c_block_data(self.addr, 0x32, 6)
        ax = _s16(d[0] | (d[1] << 8)) / 256.0
        ay = _s16(d[2] | (d[3] << 8)) / 256.0
        az = _s16(d[4] | (d[5] << 8)) / 256.0
        return ax, ay, az


class _H3LIS331DL:
    """H3LIS331DL (SparkFun SEN-14480)。±100g、12bit、約49mg/LSB。"""

    def __init__(self, bus, addr):
        self.bus, self.addr = bus, addr
        bus.write_byte_data(addr, 0x20, 0x27)  # CTRL1: normal mode, 50Hz, XYZ有効
        bus.write_byte_data(addr, 0x23, 0x00)  # CTRL4: ±100g
        time.sleep(0.05)

    def read_g(self):
        d = self.bus.read_i2c_block_data(self.addr, 0x28 | 0x80, 6)  # 自動インクリメント
        out = []
        for i in (0, 2, 4):
            raw = _s16(d[i] | (d[i + 1] << 8)) >> 4      # 左詰め12bit
            out.append(raw * 0.049)
        return tuple(out)


def _s16(v):
    return v - 65536 if v > 32767 else v


def _probe(bus):
    """接続センサーを探して初期化済みドライバを返す（なければ None）。"""
    try:  # ADXL345 @0x53
        if bus.read_byte_data(0x53, 0x00) == 0xE5:
            return _ADXL345(bus), "ADXL345@0x53"
    except OSError:
        pass
    for addr in (0x19, 0x18):  # H3LIS331DL (SEN-14480)
        try:
            if bus.read_byte_data(addr, 0x0F) == 0x32:
                return _H3LIS331DL(bus, addr), f"H3LIS331DL@{addr:#x}"
        except OSError:
            pass
    for addr in (0x69, 0x68):  # MPU 系（0x68 は PiSugar RTC と共存のため要確認）
        try:
            if bus.read_byte_data(addr, 0x75) in _MPU_WHO_AM_I_OK:
                return _MPU6050(bus, addr), f"MPU6050@{addr:#x}"
        except OSError:
            pass
    return None, None


class ShakeMonitor:
    """バックグラウンドで加速度を監視し、揺れイベントをキューに積む。

    events から "light" / "hard" を取り出して使う。
    """

    def __init__(self):
        self.events = deque(maxlen=8)
        self.present = False
        self.name = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def pop_event(self):
        try:
            return self.events.popleft()
        except IndexError:
            return None

    # ---- 内部 ----
    def _run(self):
        if SMBus is None:
            return
        while not self._stop.is_set():
            try:
                with SMBus(I2C_BUS) as bus:
                    sensor, name = _probe(bus)
                    if sensor is None:
                        self._stop.wait(RESCAN_SEC)
                        continue
                    self.present, self.name = True, name
                    self._watch(sensor)
            except OSError:
                pass
            self.present = False
            self._stop.wait(RESCAN_SEC)

    def _watch(self, sensor):
        """揺れ判定ループ。センサーが外れたら OSError で抜ける。"""
        peaks = deque()                       # (時刻, 偏差g)
        cool = {"light": 0.0, "hard": 0.0}
        above = False
        while not self._stop.is_set():
            ax, ay, az = sensor.read_g()
            dev = abs((ax * ax + ay * ay + az * az) ** 0.5 - 1.0)
            now = time.time()
            if dev > LIGHT["thresh"] and not above:   # ピークの立ち上がりのみ記録
                above = True
                peaks.append((now, dev))
            elif dev < LIGHT["thresh"] * 0.6:
                above = False
            while peaks and now - peaks[0][0] > WINDOW_SEC:
                peaks.popleft()

            hard_n = sum(1 for _, d in peaks if d > HARD["thresh"])
            if hard_n >= HARD["peaks"] and now > cool["hard"]:
                cool["hard"] = now + HARD["cooldown"]
                cool["light"] = now + HARD["cooldown"]
                peaks.clear()
                self.events.append("hard")
            elif len(peaks) >= LIGHT["peaks"] and now > cool["light"]:
                cool["light"] = now + LIGHT["cooldown"]
                peaks.clear()
                self.events.append("light")
            time.sleep(1.0 / POLL_HZ)
