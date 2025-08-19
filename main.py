

import os
import asyncio
import tempfile
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter
import uvicorn

# ------------------------------------------------------------------
# 0.  Environment
# ------------------------------------------------------------------
API_TOKEN   = os.getenv("BOT_TOKEN")   # Telegram bot token
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # https://<your-app>.leapcell.dev
WEBHOOK_PATH = "/webhook"

if not API_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("BOT_TOKEN and WEBHOOK_URL env vars are required")

# ------------------------------------------------------------------
# 1.  Logging
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mega-bot")

# ------------------------------------------------------------------
# 2.  Bot & Dispatcher
# ------------------------------------------------------------------
bot = Bot(
    API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# ------------------------------------------------------------------
# 3.  Handlers
# ------------------------------------------------------------------
START_TXT = "👋 Hello! Send me any <b>public</b> MEGA.nz link and I’ll download & upload it here."

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(START_TXT)

MEGA_PREFIXES = (
    "https://mega.nz/",
    "http://mega.nz/",
    "https://mega.co.nz/",
    "http://mega.co.nz/",
)

@dp.message(F.text.startswith(MEGA_PREFIXES))
async def handle_mega(message: Message) -> None:
    link = message.text.strip()
    status = await message.answer("⏳ Preparing download…")

    # ------------------------------------------------------------------
    # 3-a  Download section
    # ------------------------------------------------------------------
    tmpdir = Path(tempfile.mkdtemp(dir="/tmp"))
    outdir = tmpdir / "out"
    outdir.mkdir()

    try:
        await _download_public_mega(link, outdir, status)
        files = list(outdir.rglob("*"))
        files = [p for p in files if p.is_file()]
        if not files:
            await status.edit_text("⚠️ Nothing was downloaded. Is the link valid & public?")
            return

        # ------------------------------------------------------------------
        # 3-b  Upload section
        # ------------------------------------------------------------------
        await status.edit_text("📤 Uploading to Telegram…")
        for f in files:
            await _upload_file(f, message.chat.id, status)
        await status.delete()
    finally:
        # Clean up no matter what
        for p in outdir.rglob("*"):
            if p.is_file():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
        try:
            outdir.rmdir()
            tmpdir.rmdir()
        except Exception:
            pass

# ------------------------------------------------------------------
# 4.  Downloader (pure-Python, no CLI tools)
# ------------------------------------------------------------------
from mega import Mega  # pip install mega.py

async def _download_public_mega(url: str, dest: Path, status: Message) -> None:
    loop = asyncio.get_running_loop()
    mega = Mega()

    def _sync_dl():
        m = mega.login()  # anonymous
        return m.download_url(str(url), str(dest))

    await loop.run_in_executor(None, _sync_dl)

# ------------------------------------------------------------------
# 5.  Uploader
# ------------------------------------------------------------------
async def _upload_file(file_path: Path, chat_id: int, status: Message) -> None:
    try:
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(str(file_path)),
            disable_content_type_detection=True,
        )
    except TelegramRetryAfter as e:
        log.warning("Flood wait %ss for %s", e.retry_after, file_path.name)
        await asyncio.sleep(e.retry_after)
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(str(file_path)),
            disable_content_type_detection=True,
        )

# ------------------------------------------------------------------
# 6.  FastAPI webhook plumbing
# ------------------------------------------------------------------
app = FastAPI()

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/health")  # Leapcell health-check
async def health():
    return {"status": "ok"}

# ------------------------------------------------------------------
# 7.  Startup / shutdown
# ------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

# ------------------------------------------------------------------
# 8.  Entrypoint
# ------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
