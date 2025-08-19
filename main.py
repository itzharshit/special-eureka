import asyncio
import os
import tempfile
import math
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter
import uvicorn

API_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Must be full https URL
WEBHOOK_PATH = "/webhook"

bot = Bot(API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ---------------- Handlers ----------------
@dp.message(F.text.startswith("https://mega.nz/") | F.text.startswith("https://mega.co.nz/"))
async def handle_mega(message: Message) -> None:
    link = message.text.strip()
    progress_msg = await message.answer("Starting download…")
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = os.path.join(tmpdir, "out")
        os.makedirs(out_dir, exist_ok=True)
        await download_mega(link, out_dir, progress_msg)
        for root, _, files in os.walk(out_dir):
            for file in files:
                await upload_file(os.path.join(root, file), progress_msg)

async def download_mega(url: str, dest: str, msg: Message) -> None:
    proc = await asyncio.create_subprocess_exec(
        "megadl", url, "--path", dest,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    last = 0
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode().strip()
        if "%" in text:
            percent = text.split("%")[0].split()[-1]
            if percent.isdigit():
                p = int(percent)
                if p != last and p % 5 == 0:
                    last = p
                    try:
                        await msg.edit_text(f"📥 Downloading… {p}%")
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)
    await proc.wait()

async def upload_file(file_path: str, msg: Message) -> None:
    try:
        await bot.send_document(
            chat_id=msg.chat.id,
            document=FSInputFile(file_path),
            disable_content_type_detection=True
        )
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await bot.send_document(
            chat_id=msg.chat.id,
            document=FSInputFile(file_path),
            disable_content_type_detection=True
        )
    await msg.delete()

# ---------------- FastAPI + Webhook ----------------
app = FastAPI()

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)   # Convert dict → Update
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    # Must be a valid HTTPS URL accessible from Telegram
    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

# ---------------- Entry ----------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
