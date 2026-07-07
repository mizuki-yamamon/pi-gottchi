"""Gemini Live API 連続会話モード（ChatGPT音声モード風）。

起きている間はマイクを常時ストリーミングし、サーバー側VADが発話を自動検出して
音声で即応答する。ボタン操作は不要。応答再生中はマイク送信を止めて
自己エコー（スピーカー音の拾い込み）を防ぐ。

suspend()/resume() で睡眠時にマイクと通信を止められる。切断時は自動再接続。
"""
import base64
import json
import re
import subprocess
import threading
import time

_NOISE_RE = re.compile(r"<[^>]*>|[\s。、.,!?！？…・~〜ー]+")


def _real_speech(text):
    """タグ・記号を除いて2文字以上残れば「本物の発話」（「うん」もOK）。"""
    return len(_NOISE_RE.sub("", text)) >= 2

from websockets.sync.client import connect as ws_connect

try:    # 声紋ライト実験（numpy必須。無くても会話機能は動く）
    import voiceprint
except Exception:
    voiceprint = None

WS_URL = ("wss://generativelanguage.googleapis.com/ws/"
          "google.ai.generativelanguage.v1beta.GenerativeService."
          "BidiGenerateContent?key={key}")
DEFAULT_MODEL = "gemini-2.5-flash-native-audio-latest"
VOICE = "Leda"
IN_RATE = 16000
CHUNK = 16000               # 0.5秒分 (16kHz S16LE mono)。armv6のTLS/JSONコストを
                            # 1/5に減らし、送信遅延の蓄積(=聴きっぱなし化)を防ぐ
UNMUTE_DELAY = 0.15         # 再生終了からマイク再開までの猶予（残響対策）
CHUNK_SEC = CHUNK / (IN_RATE * 2)   # 1チャンクの録音秒数（0.5s）
RECV_TIMEOUT = 60           # 受信待ちの区切り。無音は正常なのでpingで生存確認して継続
REPLY_TIMEOUT = 10.0        # 話し終わり後、返事を待ってマイクを閉じる最大秒数
RECONNECT_MIN = 3.0         # 再接続の初回待ち。切断中は耳が聞こえないため短く
RECONNECT_MAX = 30.0        # 連続失敗時の上限（無料枠のレート制限を焼かない）
VP_THRESH = 0.75            # 声紋: これ未満は「知らない声」（logモードの実測で調整）
VP_KEEP_CHUNKS = 8          # 声紋判定に使う直近音声（0.5秒×8=4秒。armv6の処理時間対策）


class LiveChat:
    """常時リスニングの会話セッション。

    公開状態:
      phase: "idle"(待機) / "listen"(相手が話し中) / "think" / "play"(返答再生中)
      in_text / out_text: 現在ターンの文字起こし
      last_voice: 最後に会話が動いた時刻（睡眠判定に使う）
    """

    def __init__(self, env, persona, memory=None):
        self.env = env
        self.persona = persona
        self.memory = memory         # MokoMemory（無くても動く）
        self.model = env.get("GEMINI_LIVE_MODEL", DEFAULT_MODEL)
        self.phase = "idle"
        self.in_text = ""
        self.out_text = ""
        self.last_voice = time.time()
        self.connected = False
        self._ws = None
        self._send_lock = threading.Lock()
        self._suspended = threading.Event()
        self._stop = threading.Event()
        self._mic_mute = threading.Event()   # (旧機構・未使用)
        self._playing = False        # 再生中フラグ（receiverスレッドのみが書く）
        self._goaway = False         # サーバーの切断予告を受けたら立てる
        self._ext = False            # 反応ボイス等の外部再生中
        self._clean_after = 0.0      # この時刻まで再生をまたいだ録音を破棄（エコー対策）
        # 声紋ライト実験: off=無効 / log=一致度をログに出すだけ / gate=知らない声を聞き流す
        self.vp_mode = env.get("VOICEPRINT", "log") if voiceprint else "off"
        self._vp_ref = None          # 起こした人の声（suspend=おやすみでリセット）
        self._vp_ok = None           # このターンの声判定（None=未判定）
        self._vp_lock = threading.Lock()
        self._turn_audio = []        # 判定用の直近送信音声
        self._rec = None
        self._player = None
        self._threads = []

    # ---------- 制御 ----------
    def start(self):
        if self.vp_mode != "off":
            print(f"[voice] 声紋ライト実験: {self.vp_mode}モード")
        for fn in (self._manager_loop, self._sender_loop):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)

    def suspend(self):
        """睡眠: マイク停止+セッション切断（次の resume で張り直す）。"""
        self._suspended.set()
        self._stop_rec()
        self._stop_player()
        self._close_ws()
        self.phase = "idle"
        self._vp_ref = None          # おやすみで「起こした人の声」を忘れる

    def resume(self):
        if self._suspended.is_set():
            self._suspended.clear()
            self.last_voice = time.time()

    def close(self):
        self._stop.set()
        self.suspend()

    def active(self):
        return self.connected and not self._suspended.is_set()

    def external_mute(self, on):
        """反応ボイス等、Live外の音声再生中もマイクを止める（自己エコー防止）。"""
        on = bool(on)
        if self._ext and not on:             # 再生終了 → 境界チャンクも捨てる
            self._clean_after = time.time() + CHUNK_SEC
        self._ext = on                       # ラッチせず毎回上書き（詰まらない）

    # ---------- 内部: 接続管理 ----------
    def _manager_loop(self):
        wait = RECONNECT_MIN
        while not self._stop.is_set():
            if self._suspended.is_set() or self.connected:
                time.sleep(0.5)
                continue
            try:
                self._connect()
                wait = RECONNECT_MIN      # 接続成功でバックオフを戻す
                self._receiver()          # 切断まで戻らない
            except Exception as exc:
                print(f"[live] 接続断: {type(exc).__name__}: {exc}")
            self.connected = False
            self._close_ws()
            self._stop_player()
            self.in_text = ""            # 途中の聞き取りを捨てる（顔の固まり防止）
            self.out_text = ""
            self.phase = "idle"
            self._vp_ok = None
            self._turn_audio = []
            self._stop.wait(wait)
            wait = min(wait * 2, RECONNECT_MAX)

    def _connect(self):
        now = time.strftime("%Y年%m月%d日(%a) %H時%M分")
        gen_cfg = {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": VOICE}}},
        }
        if "native-audio" in self.model:     # 思考OFFはネイティブ音声型のみ対応
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        ws = ws_connect(WS_URL.format(key=self.env["GEMINI_API_KEY"]),
                        open_timeout=15, close_timeout=5)
        remembered = self.memory.prompt_text() if self.memory else ""
        ws.send(json.dumps({"setup": {
            "model": f"models/{self.model}",
            "generationConfig": gen_cfg,
            "systemInstruction": {"parts": [{"text": (
                self.persona + remembered
                + f"\n\n現在の日時: {now}（日本時間）。"
                "\nユーザーの発話は音声で届きます。かならず日本語で答えてください。")}]},
            "realtimeInputConfig": {
                "activityHandling": "NO_INTERRUPTION",   # 話し終わるまで中断しない
                "automaticActivityDetection": {
                    "silenceDurationMs": 300,   # 発話終了とみなす沈黙（短いほど即答）
                    # 開始感度は標準（LOWだと小声・短い呼びかけを取りこぼす）
                }
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}))
        try:
            first = json.loads(ws.recv(timeout=15))
        except Exception as exc:
            first = {"recv_error": f"{type(exc).__name__}: {exc}"}
        if "setupComplete" not in first:
            ws.close()
            raise RuntimeError(f"setup失敗: {str(first)[:200]}")
        self._ws = ws
        self.connected = True
        self._goaway = False
        print(f"[live] 連続会話セッション接続 ({self.model})")

    def _close_ws(self):
        ws, self._ws = self._ws, None
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        self.connected = False

    def _send(self, obj):
        with self._send_lock:
            ws = self._ws
            if ws is None:
                return False
            try:
                ws.send(json.dumps(obj))
                return True
            except Exception:
                return False

    # ---------- 内部: マイク送信 ----------
    def _sender_loop(self):
        while not self._stop.is_set():
            if self._suspended.is_set() or not self.connected:
                self._stop_rec()
                time.sleep(0.3)
                continue
            if self._rec is None or self._rec.poll() is not None:
                # -B 1秒: CPUが描画/TLSで詰まった瞬間のオーバーラン（録音欠落）を防ぐ
                self._rec = subprocess.Popen(
                    ["arecord", "-q", "-D", self.env.get("ALSA_DEV", "plughw:CARD=whisplaysound"),
                     "-f", "S16_LE", "-r", str(IN_RATE), "-c", "1",
                     "-B", "1000000", "-t", "raw", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            chunk = self._rec.stdout.read(CHUNK)
            if not chunk:
                self._stop_rec()
                continue
            if self._playing or self._ext:
                continue    # もこの発話中はマイク入力を破棄（半二重・キュー防止）
            if time.time() < self._clean_after:
                continue    # 再生をまたいだ録音は自分の声が混入しているため破棄
            waited = time.time() - self.last_voice
            if self.in_text and self.phase == "listen" and waited > 0.7:
                if self._vp_ok is None and self.vp_mode != "off":
                    self._judge_voice()   # 話し終わった発話の声を判定
                if waited < REPLY_TIMEOUT:
                    continue    # 話し終わり〜返事完了まで新規入力を受けない（二重質問防止）
                # 返事が来ないまま時間切れ → 聞き取りを捨ててマイクを解放（黙り込み防止）
                print("[live] 返事が来ないため聞き直します")
                self.in_text = ""
                self.phase = "idle"
            sent = self._send({"realtimeInput": {"audio": {
                "mimeType": f"audio/pcm;rate={IN_RATE}",
                "data": base64.b64encode(chunk).decode()}}})
            if sent and self.vp_mode != "off":
                self._turn_audio.append(chunk)
                del self._turn_audio[:-VP_KEEP_CHUNKS]

    def _stop_rec(self):
        rec, self._rec = self._rec, None
        if rec:
            rec.terminate()
            try:
                rec.wait(timeout=1)
            except subprocess.TimeoutExpired:
                rec.kill()

    def _judge_voice(self):
        """このターンの声が「起こした人」と同じかを判定（声紋ライト実験）。

        初回の発話で声を登録し、以後は一致度をログに出す。gateモードでは
        しきい値未満のターンの返事を再生しない。おやすみで登録は消える。
        """
        with self._vp_lock:
            if self._vp_ok is not None:
                return
            vec = voiceprint.extract(b"".join(tuple(self._turn_audio)))
            if vec is None:
                self._vp_ok = True       # 判定材料が足りないターンは通す
                return
            if self._vp_ref is None:
                self._vp_ref = vec
                self._vp_ok = True
                print("[voice] この声を覚えた（次のおやすみまで）")
                return
            sim = voiceprint.similarity(self._vp_ref, vec)
            self._vp_ok = self.vp_mode != "gate" or sim >= VP_THRESH
            print(f"[voice] 声の一致度 {sim:.2f}"
                  + ("" if self._vp_ok else " → 知らない声なので聞き流す"))

    # ---------- 内部: 受信・再生 ----------
    def _receiver(self):
        while not self._stop.is_set() and not self._suspended.is_set():
            ws = self._ws
            if ws is None:
                return
            try:
                msg = json.loads(ws.recv(timeout=RECV_TIMEOUT))
            except TimeoutError:
                # 無音中はサーバーから何も届かないのが正常。切断せずpingで生存確認
                if not ws.ping().wait(10):
                    raise RuntimeError("ping応答なし")
                continue
            if "goAway" in msg:      # サーバーのセッション期限予告
                print("[live] セッション期限予告 → 再生完了後に張り直します")
                self._goaway = True
            content = msg.get("serverContent", {})
            t = content.get("inputTranscription", {}).get("text")
            if t:
                self.in_text += t
                if _real_speech(self.in_text):   # 本物の発話のみ起きてる扱い
                    self.last_voice = time.time()
                    if self.phase == "idle":
                        self.phase = "listen"
            t = content.get("outputTranscription", {}).get("text")
            if t:
                self.out_text += t
            if content.get("interrupted"):
                continue                     # 割り込みで再生を切らない（最後まで話す）
            for part in content.get("modelTurn", {}).get("parts", []):
                data = part.get("inlineData", {}).get("data")
                if not data:
                    continue
                if not _real_speech(self.in_text):
                    continue                     # 雑音ターンの返事は黙って捨てる
                if self._vp_ok is None and self.vp_mode != "off":
                    self._judge_voice()          # 返事が先に届いた場合もここで判定
                if self._vp_ok is False:
                    continue                     # 知らない声のターンは返事を再生しない
                if self._player is None:
                    self._playing = True
                    self.phase = "play"
                    self._player = subprocess.Popen(
                        ["aplay", "-q", "-D",
                         self.env.get("ALSA_DEV", "plughw:CARD=whisplaysound"),
                         "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1", "-"],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
                try:
                    self._player.stdin.write(base64.b64decode(data))
                except (BrokenPipeError, OSError):
                    self._stop_player()
            if content.get("turnComplete"):
                self._finish_turn()
            if self._goaway and not self._playing:
                return               # きれいに張り直す（managerがすぐ再接続）

    def _finish_turn(self):
        real = _real_speech(self.in_text)
        if real:
            print(f"[you] {self.in_text.strip()}")
            print(f"[moko] {self.out_text.strip()}")
            if self.memory and self._vp_ok is not False:   # 聞き流したターンは覚えない
                self.memory.add_turn(self.in_text, self.out_text)
        player, self._player = self._player, None
        if player:
            try:
                player.stdin.close()
                player.wait(timeout=30)
            except Exception:
                player.kill()
            time.sleep(UNMUTE_DELAY)
            self._clean_after = time.time() + CHUNK_SEC
        self._playing = False
        if real:
            self.last_voice = time.time()    # 雑音ターンでは眠気を妨げない
        self.in_text = ""
        self.out_text = ""
        self.phase = "idle"
        self._vp_ok = None                   # 次のターンの声判定へ
        self._turn_audio = []

    def _stop_player(self):
        player, self._player = self._player, None
        if player:
            try:
                player.kill()
            except Exception:
                pass
            self._clean_after = time.time() + CHUNK_SEC
        self._playing = False
        if self.phase == "play":
            self.phase = "idle"
