# もこ 配線ガイド — SG90サーボ & SEN-14480加速度センサー

対象: Raspberry Pi Zero W + PiSugar Whisplay HAT + PiSugarバッテリー構成の「もこ」

---

## 0. まず結論

| つなぐもの | Piの物理ピン |
|---|---|
| **SEN-14480** (加速度) | 3.3V=**1番** / GND=**9番** / SDA=**3番** / SCL=**5番** |
| **SG90** (サーボ) | 信号=**32番** / 電源=外部5V推奨(または2番) / GND=**6番**(+外部電源GNDと共通) |

> どちらも**ソフト側は対応済み**です。加速度センサーは挿せば10秒以内に自動認識（`もこ`再起動不要）、サーボは`servo.py`でテストできます。

---

## 1. 「画面が全ピンを覆ってる」問題の解決方法

Whisplay HATは40ピンヘッダーに直接かぶさりますが、**実際に信号を使っているのは約17本**で、残りは素通しです。取り出し方は3通り:

### 方法A: スタッキングヘッダーを間に挟む（おすすめ・はんだ最小限）
```
[Whisplay HAT]
    ↑ 差し込む
[2x20 スタッキングヘッダー]  ← ピンが長く、ここの横から線を取れる
    ↑ 差し込む
[Raspberry Pi Zero]
[PiSugar バッテリー]（裏面ポゴピン）
```
- 「**2x20 スタッキングヘッダー**（連結用・ピンヘッダー拡張）」で検索。数百円。
- HATと Pi の間にできる隙間で、必要なピンの根元にジャンパー線(メス)を挿すか、はんだ付け。
- 背が足りなければ2段重ねも可。

### 方法B: Pi Zero 裏面のはんだ面から直接取り出す
- Pi Zero の裏側にはヘッダーの**はんだ付け跡（スルーホール足）**が出ています。そこへ銅線・ジャンパ線を直接はんだ付け。
- ⚠️ 裏面には**PiSugarのポゴピン接点（金色の丸パッド）**があります。ショートしないよう接点付近を避け、はんだ後はカプトンテープ等で絶縁。

### 方法C: GPIO分岐基板（HAT Hacker系）
- 「HAT Hacker HAT」「GPIO Splitter」等、1つのGPIOを2系統に分岐する基板。工作は一番きれいだが入手性と厚みが難点。

### Q. 「間に銅線を引いてもいいの？」
**OKです。**ただし以下を守ってください:
- **I2C（3番・5番）の線は20cm以内**に。長いと通信エラーの原因（今回のセンサーは基板上にプルアップ抵抗があるのでそのまま繋ぐだけでOK）
- **GNDは必ず共通に**（センサーも、サーボの外部電源も、Pi のGNDと繋ぐ）
- サーボの**信号線**は長くても平気（1m程度まで問題なし）
- 被覆付きの線を使い、金属部が他のピンに触れないように

---

## 2. SEN-14480（SparkFun H3LIS331DL 加速度センサー）

### 配線（4本だけ）
| SEN-14480側 | Pi側（物理ピン） | 備考 |
|---|---|---|
| 3V3 (VCC) | **1番** (3.3V) | ⚠️ **5Vに繋ぐと壊れます**（2.16〜3.6V専用） |
| GND | **9番** (GND) | 6/14/20/25番でも可 |
| SDA | **3番** (GPIO2/SDA) | Whisplay/PiSugarとバス共有でOK |
| SCL | **5番** (GPIO3/SCL) | 同上 |
| CS / SA0 / INT | 接続不要 | 基板上のプルアップでI2Cモードになります |

- I2Cアドレスは 0x18 または 0x19。**もこが両方自動スキャン**するのでジャンパ設定不要。
- PiSugar (0x57/0x68) や音声コーデック (0x1a) とは衝突しません。

### 接続確認
```bash
ssh mizukichi@zeropicam.local
sudo i2cdetect -y 1        # 0x18 か 0x19 が現れればOK
sudo journalctl -u moko -f # 「加速度センサー: H3LIS331DL@0x19」のtickログが出る
```
→ 本体を**振ると「もこ」がびっくり**します。

### ⚠️ このセンサーの特性（正直な話）
H3LIS331DL は **±100G〜400G の高衝撃測定用**（衝突・落下検出向け）です。分解能が約0.05G/目盛と粗く、**手で優しく揺らす検知はやや苦手**（強めに振れば反応します）。
- 感度が物足りなければ `~/whisplay-moko/imu.py` の `LIGHT`/`HARD` の `thresh` を下げて調整
- 繊細な揺れ検知をしたくなったら **MPU6050（GY-521、±2G、約200〜500円）** が最適。その場合は **AD0ピンを必ず3.3Vへ**（PiSugarのRTCとアドレス衝突を避けるため）。もこは両対応済み。

---

## 3. SG90 サーボモーター

### 配線
| SG90側（線色） | 接続先 |
|---|---|
| 信号（橙） | **32番** (BCM12) ※2個目は **33番** (BCM13) |
| 電源（赤） | **外部5V電源を推奨**（下記） |
| GND（茶） | **6番** (GND) ※外部電源使用時はそのGNDとPiのGNDを結線 |

32/33番はWhisplay HATが使っていない**ハードウェアPWMピン**なので、カクつきなくサーボを動かせます。

### 有効化（初回のみ）
```bash
ssh mizukichi@zeropicam.local
sudo nano /boot/firmware/config.txt    # 末尾に1行追記
```
```
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```
```bash
sudo reboot
```

### テスト
```bash
cd ~/whisplay-moko && sudo python3 servo.py   # 0→90→180→90度と動けば成功
```
プログラムからは:
```python
from servo import Servo
s = Servo(channel=0)   # 0=32番ピン, 1=33番ピン
s.angle(45)
```

### ⚠️ 電源の注意（いちばん大事）
- SG90は動き出しに**500mA以上**流れます。PiSugarバッテリー駆動中にPiの5Vピンから取ると、**電圧降下でPiが落ちる/再起動する**ことがあります。
- **推奨**: サーボ用に別の5V（乾電池4本+レギュレータ、モバイルバッテリー、5V ACアダプタ等）→ 赤線へ。**GNDはPiと共通に**。
- 簡易にPiの**2番ピン(5V)**から取る場合: サーボは1個まで+電源ラインに**470µF以上の電解コンデンサ**を入れると安定します。

---

## 4. ピン全体マップ（どこが空いているか）

```
        3.3V  1 ●  ● 2   5V     ← サーボ電源(簡易時)
   SDA(共有)  3 ●  ● 4   5V
   SCL(共有)  5 ●  ● 6   GND    ← サーボGND
  [HAT:RST]   7 ●  ● 8   (空き UART-TX)
        GND   9 ●  ● 10  (空き UART-RX)   ← 9番=センサーGND
  [HAT:BTN]  11 ●  ● 12  [HAT:I2S]
  [HAT:DC]   13 ●  ● 14  GND
  [HAT:BL]   15 ●  ● 16  [HAT:LED B]
       3.3V  17 ●  ● 18  [HAT:LED G]
  [HAT:SPI]  19 ●  ● 20  GND
  [HAT:SPI]  21 ●  ● 22  [HAT:LED R]
  [HAT:SPI]  23 ●  ● 24  [HAT:SPI]
        GND  25 ●  ● 26  (空き)
   (EEPROM)  27 ●  ● 28  (EEPROM・使わない)
     (空き)  29 ●  ● 30  GND
     (空き)  31 ●  ● 32  ★空き PWM0 ← サーボ1信号
 ★空き PWM1 33 ●  ● 34  GND
  [HAT:I2S]  35 ●  ● 36  (空き)
     (空き)  37 ●  ● 38  [HAT:I2S]
        GND  39 ●  ● 40  [HAT:I2S]
```
`[HAT:○○]` = Whisplay使用中 / `(空き)` = 自由に使える / I2C(3・5番)は**共有バス**なのでセンサー追加OK

---

## 5. トラブルシューティング

| 症状 | 確認すること |
|---|---|
| i2cdetectにセンサーが出ない | 配線4本の順番 / 3.3Vに繋いだか / SDA・SCL逆挿し |
| 揺らしても反応しない | journalのtickログで `imu=H3LIS331DL` になっているか / 強めに振る / imu.pyのthresh調整 |
| サーボがプルプル震える | `s.release()`で保持を切る / 電源不足（外部5Vへ） |
| サーボを動かすとPiが再起動 | 電源不足。外部5V+GND共通に変更 |
| Piが起動しなくなった | 配線を全部抜いて起動→1本ずつ戻して切り分け |

## 参考リンク
- [SparkFun H3LIS331DL Hookup Guide](https://learn.sparkfun.com/tutorials/h3lis331dl-accelerometer-breakout-hookup-guide/all)
- [SparkFun H3LIS331DL 製品ページ](https://www.sparkfun.com/sparkfun-triple-axis-accelerometer-breakout-h3lis331dl.html)
- [PiSugar Whisplay HAT Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/overview)
- Pi内の同内容ドキュメント: `~/whisplay-moko/WIRING.md`
