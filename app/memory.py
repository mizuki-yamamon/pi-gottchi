"""もこの記憶: 名前や話したことを軽く覚えて、次の会話に持ち越す。

- recent  : 直近の会話ターン（そのままプロンプトに入れる短期記憶）
- profile : 会話から抽出した長期記憶。寝ている間に Gemini で整理・統合する
memory.json に保存し、再起動・スリープをまたいで「前のことを少し覚えている」を実現する。
"""
import json
import threading

import requests

import voice

RECENT_KEEP = 10          # 保持する直近ターン数（整理の材料）
RECENT_PROMPT = 4         # プロンプトに入れる直近ターン数
MERGE_AFTER = 6           # このターン数たまったら寝入り時に記憶を整理
PROFILE_CHARS = 400       # 長期記憶の上限文字数（プロンプト肥大防止）

MERGE_PROMPT = (
    "あなたは会話ログから記憶を整理する係です。以下の「これまでの記憶」と"
    "「最近の会話」を統合し、次回の会話で役立つことだけを日本語で250文字以内に"
    "まとめてください。含める: 相手の名前や呼び方、好きなもの・嫌いなもの、"
    "暮らしの事実、約束や気にかけていること、直近の話題。"
    "含めない: 挨拶や相槌、一度きりの雑談の細部。"
    "出力は文章のみ（前置き・記号・箇条書きは不要）。"
)


class MokoMemory:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self._merging = False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.profile = str(data.get("profile", ""))[:PROFILE_CHARS]
        self.recent = [r for r in data.get("recent", []) if len(r) == 2][-RECENT_KEEP:]
        self.pending = int(data.get("pending", 0))

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"profile": self.profile, "recent": self.recent,
                           "pending": self.pending}, f, ensure_ascii=False)
        except OSError:
            pass

    def prompt_text(self):
        """システムプロンプトに足す記憶ブロック（接続・起床のたびに最新を注入）。"""
        with self.lock:
            parts = []
            if self.profile:
                parts.append(f"\n\n【もこが覚えていること】\n{self.profile}")
            if self.recent:
                lines = "\n".join(f"相手「{u}」→もこ「{m}」"
                                  for u, m in self.recent[-RECENT_PROMPT:])
                parts.append(f"\n\n【直前までの会話】\n{lines}")
            if parts:
                parts.append("\n覚えていることは、聞かれたら答えるほか、"
                             "会話が続いているように自然に活かすこと。")
            return "".join(parts)

    def add_turn(self, user, moko):
        user, moko = (user or "").strip(), (moko or "").strip()
        if not user or not moko:
            return
        with self.lock:
            self.recent.append([user[:120], moko[:120]])
            self.recent = self.recent[-RECENT_KEEP:]
            self.pending += 1
            self._save()

    def consolidate_async(self, env):
        """たまった会話を長期記憶へ統合する（寝入り時に呼ぶ。裏スレッドで実行）。"""
        if self._merging or self.pending < MERGE_AFTER:
            return
        if env.get("_provider") != "gemini":
            return
        self._merging = True
        threading.Thread(target=self._merge, args=(env,), daemon=True).start()

    def _merge(self, env):
        try:
            with self.lock:
                log = "\n".join(f"相手「{u}」もこ「{m}」" for u, m in self.recent)
                profile = self.profile
            text = voice.gemini_text(
                f"{MERGE_PROMPT}\n\n【これまでの記憶】\n{profile or '（まだ何もない）'}"
                f"\n\n【最近の会話】\n{log}", env)
            if text:
                with self.lock:
                    self.profile = text[:PROFILE_CHARS]
                    self.pending = 0
                    self._save()
                print(f"[memory] ねむりながら記憶を整理した（{len(text)}文字）")
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            print(f"[memory] 整理に失敗（次の機会に再挑戦）: {exc}")
        finally:
            self._merging = False
