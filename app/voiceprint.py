"""声紋ライト: MFCC統計で「起こした人の声」をゆるく見分ける実験機能。

ARMv6 (Pi Zero W) ではニューラル話者認識が動かないため、古典的な
MFCC平均+標準偏差のコサイン類似度で代用する。精度はおもちゃレベル:
声質がはっきり違う相手（大人/子供、肉声/テレビ）向け。同性の似た声は苦手。
"""
import numpy as np

RATE = 16000
N_FFT = 512
FRAME = 400          # 25ms
HOP = 160            # 10ms
N_MEL = 24
N_COEF = 12          # c1..c12（c0=音量は声でなく距離に依存するので除外）


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _mel_fb():
    pts = _mel_to_hz(np.linspace(_hz_to_mel(80.0), _hz_to_mel(7600.0), N_MEL + 2))
    bins = np.floor((N_FFT + 1) * pts / RATE).astype(int)
    fb = np.zeros((N_MEL, N_FFT // 2 + 1), dtype=np.float32)
    for i in range(N_MEL):
        a, b, c = bins[i], bins[i + 1], bins[i + 2]
        b = max(b, a + 1)
        c = max(c, b + 1)
        fb[i, a:b] = np.linspace(0, 1, b - a, endpoint=False)
        fb[i, b:c] = np.linspace(1, 0, c - b, endpoint=False)
    return fb


_FB = _mel_fb()
_WIN = np.hanning(FRAME).astype(np.float32)
# DCT-II の k=1..12 行（MFCC標準形）
_DCT = np.cos(np.pi / N_MEL * (np.arange(N_MEL)[None, :] + 0.5)
              * np.arange(1, N_COEF + 1)[:, None]).astype(np.float32)


def extract(pcm):
    """S16LE 16kHz mono PCM → 声紋ベクトル(24次元)。材料不足なら None。"""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size < FRAME + HOP * 20:            # 0.2秒ぶんのフレームも無い
        return None
    n = 1 + (x.size - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * _WIN
    # 有声フレームだけ使う（無音・小さな環境音を除外）。
    # ピーク比で選ぶ: 中央値比だと休みなく話す音声で全フレームが落ちる
    energy = frames.std(axis=1)
    peak = float(np.percentile(energy, 95))
    keep = energy > max(peak * 0.2, 0.008)
    if int(keep.sum()) < 15:                 # 話し声が実質入っていない
        return None
    spec = np.abs(np.fft.rfft(frames[keep], N_FFT, axis=1)) ** 2
    mfcc = np.log(spec @ _FB.T + 1e-8) @ _DCT.T          # (フレーム, 12)
    vec = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)])
    vec -= vec.mean()
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else None


def similarity(a, b):
    """コサイン類似度（-1〜1。同一人物の目安は実測ログで決める）。"""
    return float(np.dot(a, b))
