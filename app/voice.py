"""「もこ」の声まわり: 音声認識 / 会話生成 / 可愛い声の TTS / 反応クリップのキャッシュ.

プロバイダは .env のキーで自動選択:
  - GEMINI_API_KEY があれば Gemini（無料枠OK・聞き取り+会話は1コールで完結）
  - なければ OpenAI(Whisper/TTS) + Anthropic(Claude)
APIキーはハードコード禁止。TTS には「可愛い声」の演技指示を付ける。
"""
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time

import requests

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_CHAT_MODEL = "gemini-2.5-flash"
GEMINI_TTS_MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts")
GEMINI_VOICE = "Leda"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"

TTS_STYLE = (
    "小さくてまるい、ふわふわの可愛いマスコットキャラクター「もこ」の声。"
    "高めのトーンで明るく、あどけなく、元気いっぱいに可愛らしく"
)

SYSTEM_PROMPT = (
    "あなたは「もこ」。ラズベリーパイの小さな画面の中に住む、まるくてふわふわの"
    "可愛い生き物です。一人称は「もこ」。明るく無邪気で、やわらかい話し言葉で話します。"
    "語尾は「〜だよ」「〜なの」「〜ね!」のように可愛らしく。"
    "\n会話のルール:"
    "\n- 相手の言った内容に必ず具体的にふれて、1〜3文で返す。ときどき短い質問をかえして会話をつなげる"
    "\n- 音声がノイズだけ・無音・聞き取れない時は、適当に相槌せず「ん?よくきこえなかったよ、もういっかい!」とだけ聞き返す"
    "\n- 声で読み上げるため、記号・絵文字・箇条書き・URL・英単語の羅列は使わない"
    "\n- 知らないことは「わかんないや、ごめんね」と正直に言う"
)

# 反応ボイス（初回起動時に一括生成してキャッシュ。以後はオフラインで再生可能）
CLIPS = {
    "greet": "もこだよ!きょうもいっしょにあそぼうね!",
    "petted_0": "えへへ、なでなでうれしいなあ",
    "petted_1": "くすぐったいよお",
    "petted_2": "もっとなでてもいいよ?",
    "shake_light_0": "わわっ、ゆれてるゆれてる!",
    "shake_light_1": "ふわあ、なんだかたのしいね!",
    "shake_hard_0": "ひゃあー!めがまわるよお",
    "shake_hard_1": "ゆ、ゆらしすぎだよお!",
    "wake": "ふぁあ…おはよう…",
    "sleepy": "もこ、ねむくなっちゃった。おやすみなさい",
    "hi_0": "よんだ?",
    "hi_1": "なあに?",
    "hungry_0": "おなかすいたよぉ…じゅうでんしてほしいな",
    "hungry_1": "でんちがへっちゃった。もぐもぐさせて?",
    "eat_0": "わーい、ごはんだ!もぐもぐ…でんき、おいしい!",
    "eat_1": "いただきまーす!もぐもぐもぐ…",
    "batt_low": "でんちがもうすぐなくなっちゃう。おやすみするね、じゅうでんしてね",
    "no_hear": "うまくきこえなかったよ。もういっかいおしえて!",
    "no_net": "うーん、インターネットにつながらないみたい。ごめんね",
}


# ---------- 設定 ----------
def _valid_key(v):
    """未記入・プレースホルダー・非ASCIIは無効扱い。"""
    return bool(v) and all(ord(c) < 128 for c in v)


def load_env(*paths):
    """最初に見つかった .env を読む。キー未設定でも例外にしない（オフライン動作可）。"""
    env = {"_path": None, "_voice_ok": False, "_provider": None}
    path = next((p for p in paths if os.path.exists(p)), None)
    if path is None:
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip('"').strip("'")
    env["_path"] = path
    if _valid_key(env.get("GEMINI_API_KEY")):
        env["_provider"] = "gemini"
    elif (_valid_key(env.get("ANTHROPIC_API_KEY"))
          and _valid_key(env.get("OPENAI_API_KEY"))):
        env["_provider"] = "openai+claude"
    env["_voice_ok"] = env["_provider"] is not None
    return env


# ---------- Gemini ----------
_session = requests.Session()      # keep-alive でTLSハンドシェイクを使い回す
_last_prewarm = 0.0


def prewarm(env):
    """録音中に呼んでTLS接続を張っておく（応答の体感を短縮）。"""
    global _last_prewarm
    if env.get("_provider") != "gemini" or time.time() - _last_prewarm < 45:
        return
    _last_prewarm = time.time()
    try:
        _session.get(f"{GEMINI_BASE}?pageSize=1",
                     headers={"x-goog-api-key": env["GEMINI_API_KEY"]}, timeout=10)
    except requests.RequestException:
        pass


def _gemini_post(model, key, payload, timeout=90):
    return _session.post(
        f"{GEMINI_BASE}/{model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json=payload, timeout=timeout,
    )


def gemini_text(prompt, env, max_tokens=400):
    """1発のテキスト生成（記憶の整理などの内部処理用）。"""
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    model = env.get("GEMINI_MODEL", GEMINI_CHAT_MODEL)
    resp = _gemini_post(model, env["GEMINI_API_KEY"], payload)
    if resp.status_code == 400:               # thinkingConfig 非対応モデル向け
        del payload["generationConfig"]["thinkingConfig"]
        resp = _gemini_post(model, env["GEMINI_API_KEY"], payload)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def gemini_converse(wav_path, history, env, extra=""):
    """録音を渡して「聞き取り+返事」を1コールで得る。(user_text, reply) を返す。"""
    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    contents = [{"role": "model" if m["role"] == "assistant" else "user",
                 "parts": [{"text": m["content"]}]} for m in history]
    contents.append({"role": "user", "parts": [
        {"inlineData": {"mimeType": "audio/wav", "data": audio_b64}},
    ]})
    now = time.strftime("%Y年%m月%d日(%a) %H時%M分")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + extra + (
            f"\n\n現在の日時: {now}（日本時間）。"
            "\n\nユーザーの発話は音声で渡されます。JSON で {\"kikitori\": \"...\", "
            "\"henji\": \"...\"} だけを返します。kikitori には音声から聞き取った"
            "発話内容だけを一字一句入れます（指示文や説明は含めない）。"
            "henji はその発話へのもことしての返事。聞き取れなければ kikitori は空文字。")}]},
        "contents": contents,
        "generationConfig": {"responseMimeType": "application/json",
                             "maxOutputTokens": 300,
                             "thinkingConfig": {"thinkingBudget": 0}},  # 思考モードOFFで高速化
    }
    model = env.get("GEMINI_MODEL", GEMINI_CHAT_MODEL)
    resp = _gemini_post(model, env["GEMINI_API_KEY"], payload)
    if resp.status_code == 400:               # thinkingConfig 非対応モデル向け
        del payload["generationConfig"]["thinkingConfig"]
        resp = _gemini_post(model, env["GEMINI_API_KEY"], payload)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text)
    return data.get("kikitori", "").strip(), data.get("henji", "").strip()


def _pcm_to_wav(pcm, out_path, rate=24000):
    """生PCM(16bit mono)にWAVヘッダを付けて保存。"""
    with open(out_path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
        f.write(b"data" + struct.pack("<I", len(pcm)))
        f.write(pcm)


def _gemini_tts_once(model, text, key, out_path):
    payload = {
        "contents": [{"parts": [{"text": f"{TTS_STYLE}言って: {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}},
        },
    }
    resp = _gemini_post(model, key, payload)
    if resp.status_code in (404, 429, 503):
        return resp.status_code
    resp.raise_for_status()
    part = resp.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    rate = 24000
    for token in part.get("mimeType", "").split(";"):
        if token.strip().startswith("rate="):
            rate = int(token.split("=")[1])
    _pcm_to_wav(base64.b64decode(part["data"]), out_path, rate)
    return 200


def gemini_tts(text, env, out_path, patient=True):
    """モデル候補を順に試す。patient=True なら無料枠の429を待って再試行、
    False なら即失敗して呼び出し側のフォールバックに任せる（会話用）。"""
    key = env["GEMINI_API_KEY"]
    models = [env.get("GEMINI_TTS_MODEL")] if env.get("GEMINI_TTS_MODEL") else []
    models += [m for m in GEMINI_TTS_MODELS if m not in models]
    for model in models:
        for attempt in range(3 if patient else 1):
            code = _gemini_tts_once(model, text, key, out_path)
            if code == 200:
                return
            if code == 404:
                break                        # 次のモデル候補へ
            if patient:
                time.sleep(20 * (attempt + 1))   # 429/503: 無料枠のRPM待ち
    raise requests.RequestException("Gemini TTS が利用できませんでした")


# ---------- Open JTalk（ローカル・無料・無制限のフォールバック） ----------
OPENJTALK_DIC = "/var/lib/mecab/dic/open-jtalk/naist-jdic"
OPENJTALK_VOICE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "htsvoice", "tohoku-f01-happy.htsvoice")


def openjtalk_available():
    return (shutil.which("open_jtalk") is not None
            and os.path.exists(OPENJTALK_VOICE) and os.path.isdir(OPENJTALK_DIC))


def openjtalk_tts(text, out_path):
    """Pi ローカルで即合成（東北f01 happy + ピッチ高めで可愛く）。"""
    subprocess.run(
        ["open_jtalk", "-x", OPENJTALK_DIC, "-m", OPENJTALK_VOICE,
         "-r", "1.05", "-fm", "4", "-ow", out_path],
        input=text.encode(), timeout=90, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- OpenAI / Anthropic（Geminiキーが無い場合の経路） ----------
def transcribe(wav_path, api_key):
    with open(wav_path, "rb") as f:
        resp = requests.post(
            OPENAI_STT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(wav_path), f, "audio/wav")},
            data={"model": "whisper-1", "language": "ja"},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def ask_claude(messages, api_key, model, extra=""):
    resp = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 300,
              "system": SYSTEM_PROMPT + extra, "messages": messages},
        timeout=60,
    )
    resp.raise_for_status()
    blocks = resp.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def openai_tts(text, api_key, out_path):
    payload = {"model": "gpt-4o-mini-tts", "voice": "coral", "input": text,
               "instructions": TTS_STYLE + "話してください。",
               "response_format": "wav"}
    resp = requests.post(
        OPENAI_TTS_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "content-type": "application/json"},
        json=payload, timeout=90,
    )
    if resp.status_code in (400, 403, 404):
        resp = requests.post(
            OPENAI_TTS_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "content-type": "application/json"},
            json={"model": "tts-1", "voice": "nova", "input": text,
                  "response_format": "wav"},
            timeout=90,
        )
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)


# ---------- プロバイダ共通入口 ----------
def synthesize(text, env, out_path, patient=False):
    """可愛い声のTTS。会話(patient=False)はGemini制限時に即Open JTalkへ切替。
    キャッシュ用クリップ(patient=True)は品質優先でGeminiを待つ。"""
    if env["_provider"] == "gemini":
        try:
            gemini_tts(text, env, out_path, patient=patient)
            return
        except requests.RequestException:
            if patient or not openjtalk_available():
                raise
            print("[tts] Gemini無料枠の制限中 → Open JTalkで即時合成")
        openjtalk_tts(text, out_path)
    else:
        openai_tts(text, env["OPENAI_API_KEY"], out_path)


# ---------- 反応クリップのキャッシュ ----------
class ClipBank:
    """CLIPS の音声を voices/ にキャッシュし、名前(接頭辞)で再生する。"""

    def __init__(self, voices_dir, alsa_dev):
        self.dir = voices_dir
        self.alsa = alsa_dev
        os.makedirs(voices_dir, exist_ok=True)

    def _path(self, name, text, provider):
        digest = hashlib.sha1(f"{provider}:{TTS_STYLE}:{text}".encode()).hexdigest()[:10]
        return os.path.join(self.dir, f"{name}_{digest}.wav")

    def missing(self, provider):
        return {n: t for n, t in CLIPS.items()
                if not os.path.exists(self._path(n, t, provider))}

    def generate_missing(self, env, progress=None):
        """未生成クリップを作る。ネット無し等の例外は呼び出し側で握る。"""
        provider = env["_provider"]
        miss = self.missing(provider)
        for i, (name, text) in enumerate(sorted(miss.items())):
            if progress:
                progress(i + 1, len(miss), name)
            synthesize(text, env, self._path(name, text, provider), patient=True)

    def play(self, prefix, variant_seed=0, provider=None):
        """prefix に一致するクリップを1つ非同期再生。無ければ None。"""
        names = sorted(n for n in CLIPS if n == prefix or n.startswith(prefix + "_"))
        if not names:
            return None
        name = names[variant_seed % len(names)]
        path = self._path(name, CLIPS[name], provider)
        if not os.path.exists(path):
            return None
        return subprocess.Popen(
            ["aplay", "-q", "-D", self.alsa, path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
