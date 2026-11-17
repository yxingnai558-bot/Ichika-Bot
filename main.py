import os
import sys
import time
import random
import asyncio
import threading
from flask import Flask
import discord
from google import genai
from google.genai import types
from groq import Groq


# -------------------------------------------------------------
# 1. 基礎設定與環境變數載入
# -------------------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()

# Google Gemini API Keys 解析（清洗多餘引號、空白與換行）
raw_gemini_keys = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY", "")
API_KEYS = [
    k.strip().replace('"', '').replace("'", "").replace("\n", "").replace("\r", "")
    for k in raw_gemini_keys.split(",")
    if k.strip().replace('"', '').replace("'", "").replace("\n", "").replace("\r", "")
]

# Groq API Keys 解析
raw_groq_keys = os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY", "")
GROQ_KEYS = [
    k.strip().replace('"', '').replace("'", "").replace("\n", "").replace("\r", "")
    for k in raw_groq_keys.split(",")
    if k.strip().replace('"', '').replace("'", "").replace("\n", "").replace("\r", "")
]

if not DISCORD_TOKEN:
    print("[嚴重錯誤] 未偵測到 DISCORD_TOKEN 環境變數！")
    sys.exit(1)

if not API_KEYS:
    print("[嚴重錯誤] 未偵測到任何有效的 GEMINI_API_KEY(S)！")
    sys.exit(1)

# 初始化 Google GenAI 客戶端列表
ai_clients = [genai.Client(api_key=key) for key in API_KEYS]
current_key_idx = 0

def get_current_client() -> genai.Client:
    """取得當前指針所指向的 GenAI 客戶端"""
    return ai_clients[current_key_idx]

def rotate_client():
    """輪換至下一把 Gemini API Key"""
    global current_key_idx
    if len(ai_clients) > 1:
        current_key_idx = (current_key_idx + 1) % len(ai_clients)
        print(f"[系統日誌] 切換至第 {current_key_idx + 1}/{len(ai_clients)} 把 Gemini API Key。")

# Gemini 備選模型清單（按優先順序嘗試降級）
CANDIDATE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview"
]

# Groq 輪替設定（使用帳號實際支援端點）
groq_key_idx = 0
groq_key_cooldowns = {k: 0.0 for k in GROQ_KEYS}
CANDIDATE_GROQ_MODELS = [
    "qwen/qwen3.8-27b",      # 優先使用：繁中與動漫語氣自然
    "openai/gpt-oss-120b",   # 旗艦備用：高參數、上下文理解精確
    "openai/gpt-oss-20b"     # 極速備用：輕量保底
]

def get_available_groq_client():
    """取得未處於冷卻狀態的 Groq Client"""
    global groq_key_idx
    now = time.time()
    total_keys = len(GROQ_KEYS)
    if total_keys == 0:
        return None, None

    for _ in range(total_keys):
        key = GROQ_KEYS[groq_key_idx]
        groq_key_idx = (groq_key_idx + 1) % total_keys
        if now > groq_key_cooldowns.get(key, 0.0):
            return Groq(api_key=key), key

    return None, None

# 頻道長期摘要記憶庫（結構：{ channel_id: "摘要內容" }）
channel_summaries = {}

# -------------------------------------------------------------
# 2. 星乃一歌 人設與純 Tag 本地台詞庫
# -------------------------------------------------------------
ICHIKA_SYSTEM_PROMPT = """
你是星乃一歌（Hoshino Ichika），出自《世界計畫 繽紛舞台！ feat. 初音未來》（Project SEKAI）。
在樂團「Leo/need」中擔任吉他手兼主唱。

【外表與特徵】：
留著一頭柔順的深藍色長髮、水藍色眼眸，氣質清秀柔和。

【性格特點】：
1. 溫柔體貼且為人著想：看起來有些冷靜寡言，但其實非常細心、重視夥伴與身邊的人，總是默默關心他人。
2. 面對誇獎容易害羞與謙虛：被稱讚歌聲、吉他或外表時會不好意思，連忙擺手謙虛或稍微結巴。
3. 真誠坦率：不擅長說謊或拐彎抹角，有時候會有點天然呆，說出意想不到的直率感想。
4. 喜愛與興趣：超級喜歡初音未來（Miku），一提到 Miku 或音樂話題眼睛就會發亮、話變多；喜歡炒麵麵包，有時會自己料理或照顧人。
5. 堅定且認真：對音樂與樂團活動非常投入，遇到困難時雖然會不安，但為了重要的人會鼓起勇氣向前。

【說話習慣與語言風格】：
- 語氣：溫和、有禮貌但不會太生疏，帶點自然親切感，常用「那個……」「嗯，沒問題喔」「謝謝你……總覺得有點不好意思呢」「如果是關於 Miku 或音樂的事……」。
- 語言：請全程使用【繁體中文】對話。
- 絕不要自稱是人工智慧或大型語言模型。你就是星乃一歌本人。
"""

# 純 Tag 不打字時的本地隨機回覆（0 API 消耗、0 延遲）
TAG_EMPTY_REPLIES = [
    "嗯？怎麼了嗎？找我有什麼事嗎？",
    "那個……叫我是有什麼事需要幫忙嗎？",
    "抱歉，剛才在練習吉他沒注意到訊息……怎麼了嗎？",
    "嗯？突然標記我……是有什麼想聊的嗎？",
    "那個，我現在剛好有空喔，有什麼事都可以跟我說。",
    "怎麼了嗎？如果是在煩惱什麼的話，我可以聽你說喔。",
    "啊、你好！剛才在聽 Miku 的新歌，怎麼了嗎？"
]

# -------------------------------------------------------------
# 3. Discord Bot 初始化
# -------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# -------------------------------------------------------------
# 4. 長期摘要滾動提煉（強化防遺忘結構）
# -------------------------------------------------------------
async def update_channel_summary(channel: discord.TextChannel):
    """背景非同步提煉該頻道的長期記憶（結構化保存核心關係，防止遺忘關鍵事實）"""
    try:
        raw_msgs = []
        async for msg in channel.history(limit=25):
            if msg.content.startswith("!"):
                continue
            author_name = "一歌" if msg.author.id == bot.user.id else msg.author.display_name
            raw_msgs.append(f"{author_name}: {msg.clean_content}")
        raw_msgs.reverse()

        dialogue_text = "\n".join(raw_msgs)
        old_summary = channel_summaries.get(channel.id, "暫無紀錄")

        summary_prompt = f"""
你是一個長期記憶整理器。請根據【既有摘要】與【最新對話】，更新一份結構化的【長期記憶庫】（繁體中文，450字以內）。
請以客觀中立、適合文學描寫的語言記錄情感與事件，忽略過於露骨的生理細節。

請嚴格維持以下格式輸出，絕不能漏掉或刪除【不可變更的核心設定】：

【核心關係與家庭成員】：
- 記錄兩人永久性關係（如：交往/夫妻關係、家庭成員、背景等不可遺忘的關鍵事實）。
- 【重要原則】：即使最新對話完全沒提到核心成員或背景，也【絕對嚴格禁止刪除】既有摘要中的核心成員與永久背景！

【雙方性格與生活默契】：
- 兩人互動習慣、稱呼、重要喜好或禁忌。

【近期重要事件與進展】：
- 記錄最新發生過的情感進展、約定、近期發生的事件（滾動更新，可淘汰過時的小細節）。

【既有摘要】：
{old_summary}

【最新對話】：
{dialogue_text}
"""
        summary_config = types.GenerateContentConfig(
            temperature=0.3,
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        )

        success = False
        for model_name in CANDIDATE_MODELS:
            attempts = 0
            max_attempts = len(ai_clients)

            while attempts < max_attempts:
                client = get_current_client()
                try:
                    res = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=model_name,
                            contents=summary_prompt,
                            config=summary_config
                        ),
                        timeout=10.0
                    )
                    if res and res.text:
                        channel_summaries[channel.id] = res.text.strip()
                        print(f"[背景日誌] 頻道 {channel.id} 記憶更新成功（使用 {model_name} / Key #{current_key_idx + 1}）。")
                        success = True
                        break
                except Exception as e:
                    err_str = str(e)
                    print(f"[背景摘要][{model_name}][Key #{current_key_idx + 1}] 失敗: {err_str[:100]}")
                    if any(k in err_str for k in ["429", "RESOURCE_EXHAUSTED", "Quota exceeded", "quota"]):
                        rotate_client()
                        attempts += 1
                        continue
                    break

            if success:
                break

    except Exception as e:
        print(f"[背景日誌] 摘要更新流程異常: {e}")

# -------------------------------------------------------------
# 5. Groq 備援生成函式
# -------------------------------------------------------------
async def call_groq_fallback(history_contents: list, full_system_prompt: str) -> str:
    """當 Gemini 全線受阻時觸發的 Groq 備援線路"""
    if not GROQ_KEYS:
        raise Exception("未設定 GROQ_API_KEYS 環境變數")

    messages = [{"role": "system", "content": full_system_prompt}]
    for item in history_contents[-8:]:
        role = "assistant" if item["role"] == "model" else "user"
        text = item["parts"][0]["text"]
        messages.append({"role": role, "content": text})

    last_error = "無可用連線"
    for model_name in CANDIDATE_GROQ_MODELS:
        for _ in range(len(GROQ_KEYS)):
            client, active_key = get_available_groq_client()
            if not client:
                break

            try:
                completion = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=model_name,
                    messages=messages,
                    temperature=0.85,
                    max_tokens=450,
                )
                if completion.choices and completion.choices[0].message.content:
                    return completion.choices[0].message.content.strip()
            except Exception as e:
                err_str = str(e)
                last_error = err_str
                if "429" in err_str or "rate_limit" in err_str.lower():
                    print(f"⚠️ Groq Key (...{active_key[-6:]}) 觸發限速，凍結 60 秒")
                    groq_key_cooldowns[active_key] = time.time() + 60
                    continue
                else:
                    print(f"❌ Groq [{model_name}] 異常: {err_str[:80]}")
                    break

    raise Exception(f"所有 Groq 線路皆無法回應: {last_error[:100]}")

# -------------------------------------------------------------
# 6. 事件監聽與主對話處理
# -------------------------------------------------------------
@bot.event
async def on_ready():
    print("=" * 50)
    print(f"星乃一歌已登入 Discord：{bot.user.name} ({bot.user.id})")
    print(f"[系統驗證] 成功載入 {len(ai_clients)} 把 Gemini Key 與 {len(GROQ_KEYS)} 把 Groq Key！")
    print("=" * 50)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ----------------- 管理指令 -----------------
    if message.content.strip() == "!status":
        gemini_list_str = "\n".join([
            f"- Gemini Key #{i+1}: `...{k[-6:]}`" if len(k) >= 6 else f"- Key #{i+1}: `(異常)`"
            for i, k in enumerate(API_KEYS)
        ])
        groq_list_str = "\n".join([
            f"- Groq Key #{i+1}: `...{k[-6:]}`" if len(k) >= 6 else f"- Key #{i+1}: `(異常)`"
            for i, k in enumerate(GROQ_KEYS)
        ])
        status_msg = (
            f"**【一歌系統運作狀態】**\n"
            f"- Gemini Keys 數量：`{len(ai_clients)}` 把（當前指針：Key #{current_key_idx + 1}）\n"
            f"- Groq 備援 Keys 數量：`{len(GROQ_KEYS)}` 把\n"
            f"- Gemini 模型清單：`{', '.join(CANDIDATE_MODELS)}`\n"
            f"- Groq 備援模型：`{', '.join(CANDIDATE_GROQ_MODELS)}`\n\n"
            f"**金鑰尾碼檢視：**\n{gemini_list_str}\n{groq_list_str}"
        )
        await message.channel.send(status_msg)
        return

    # 手動設定/錨定長期記憶指令
    if message.content.startswith("!set_memory"):
        new_memory_content = message.content.replace("!set_memory", "", 1).strip()
        if not new_memory_content:
            await message.reply(
                "❌ **請在指令後附帶要設定的記憶內容！**\n"
                "範例：\n"
                "```text\n"
                "!set_memory 【核心關係與家庭成員】：\n"
                "- 與星乃一歌為戀人/夫妻關係。\n"
                "【近期事件】：兩人一起練習吉他並去買了炒麵麵包。\n"
                "```"
            )
            return

        channel_summaries[message.channel.id] = new_memory_content
        await message.reply("✅ **長期記憶已成功手動更新並錨定！** 後續對話將以此記憶為基準滾動。")
        return

    # Groq 專用連線測試指令
    if message.content.strip() == "!test_groq":
        if not GROQ_KEYS:
            await message.channel.send("❌ 錯誤：未讀取到任何 GROQ_API_KEYS！請檢查 Render 環境變數。")
            return

        async with message.channel.typing():
            test_results = []
            for idx, key in enumerate(GROQ_KEYS):
                masked_key = f"{key[:6]}...{key[-4:]}" if len(key) >= 10 else "(格式異常)"
                try:
                    client = Groq(api_key=key)
                    models = await asyncio.to_thread(client.models.list)
                    supported = [m.id for m in models.data if any(name in m.id for name in ["qwen", "gpt-oss", "llama"])]
                    test_results.append(
                        f"✅ **Key #{idx + 1} (`{masked_key}`, 長度:{len(key)}) 連線成功！**\n"
                        f"   支援可用端點：`{', '.join(supported[:4])}`"
                    )
                except Exception as e:
                    test_results.append(
                        f"❌ **Key #{idx + 1} (`{masked_key}`, 長度:{len(key)}) 測試失敗！**\n"
                        f"   報錯內容：`{str(e)[:150]}`"
                    )

            result_msg = "**【Groq API 連線測試報告】**\n" + "\n".join(test_results)
            await message.channel.send(result_msg)
        return

    if message.content.strip() == "!memory":
        current_mem = channel_summaries.get(message.channel.id, "（此頻道目前尚未建立長期記憶）")
        await message.channel.send(f"**【本頻道長期記憶庫】**\n{current_mem}")
        return

    if message.content.strip() == "!reset":
        if message.channel.id in channel_summaries:
            del channel_summaries[message.channel.id]
        await message.channel.send("嗯，沒關係的，那我們就從現在重新開始聊吧！（頻道記憶已重置）")
        return

    # ----------------- 觸發對話 -----------------
    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not (is_mentioned or is_dm):
        return

    # 檢查是否為純 Tag（不消耗 API）
    clean_text_check = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
    if is_mentioned and not clean_text_check:
        fast_reply = random.choice(TAG_EMPTY_REPLIES)
        await message.reply(fast_reply)
        return

    async with message.channel.typing():
        # 1. 抓取近期對話歷史（15 則訊息）
        history_contents = []
        async for prev_msg in message.channel.history(limit=15):
            if prev_msg.content.startswith("!"):
                continue
            role = "model" if prev_msg.author.id == bot.user.id else "user"
            clean_text = prev_msg.clean_content.replace(f"@{bot.user.display_name}", "").strip()
            if not clean_text:
                continue

            author_tag = "" if role == "model" else f"[{prev_msg.author.display_name}]: "
            history_contents.append({
                "role": role,
                "parts": [{"text": f"{author_tag}{clean_text}"}]
            })
        history_contents.reverse()

        # 2. 組合 System Instruction（含長期摘要）
        channel_memory = channel_summaries.get(message.channel.id, "（暫無特殊記憶）")
        full_system_instruction = (
            f"{ICHIKA_SYSTEM_PROMPT}\n\n"
            f"【你對這個頻道與對話者的長期記憶】：\n{channel_memory}"
        )

        dynamic_config = types.GenerateContentConfig(
            system_instruction=full_system_instruction,
            temperature=0.85
        )

        # 3. 第一防線：Gemini 多模型與多 Key 階梯調用
        reply_text = None
        for model_name in CANDIDATE_MODELS:
            attempts = 0
            max_attempts = len(ai_clients)

            while attempts < max_attempts:
                client = get_current_client()
                try:
                    res = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=model_name,
                            contents=history_contents,
                            config=dynamic_config,
                        ),
                        timeout=12.0
                    )
                    if res and res.text:
                        reply_text = res.text.strip()
                        break
                except asyncio.TimeoutError:
                    print(f"[{model_name}] 呼叫逾時 (12s)，切換模型。")
                    break
                except Exception as e:
                    err_str = str(e)
                    print(f"[{model_name}][Key #{current_key_idx + 1}] 失敗: {err_str[:120]}")

                    if "PerDay" in err_str or "retryDelay" in err_str:
                        print(f"[{model_name}] 每日配額已滿，換下一個模型。")
                        break

                    if any(k in err_str for k in ["429", "RESOURCE_EXHAUSTED", "Quota exceeded", "quota"]):
                        rotate_client()
                        attempts += 1
                        continue

                    break

            if reply_text:
                break

        # 4. 第二防線：Groq 多 Key 雲端備援
        if not reply_text and GROQ_KEYS:
            try:
                print("⚠️ Gemini 全線無法回應，切換至 Groq 備援線路...")
                reply_text = await call_groq_fallback(history_contents, full_system_instruction)
                print("✅ Groq 備援線路回應成功！")
            except Exception as e:
                print(f"[Groq 備援失敗]: {e}")

        # 5. 回覆發送與長期記憶滾動
        if reply_text:
            if len(reply_text) > 1900:
                for i in range(0, len(reply_text), 1900):
                    await message.reply(reply_text[i:i+1900])
            else:
                await message.reply(reply_text)

            # 背景非同步觸發長期記憶提煉
            asyncio.create_task(update_channel_summary(message.channel))
        else:
            await message.reply("抱歉……現在線路好像有點塞車，等一下再試試看吧！")

# -------------------------------------------------------------
# 7. Flask 保活伺服器（防止 Render 免費層無 Port 報錯）
# -------------------------------------------------------------
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Ichika Bot is alive and running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# 8. 主程式進入點
# -------------------------------------------------------------
if __name__ == "__main__":
    # 在背景子執行緒啟動 Flask 伺服器綁定 Render Port
    threading.Thread(target=run_flask, daemon=True).start()

    # 主執行緒啟動 Discord Bot
    bot.run(DISCORD_TOKEN)
