import asyncio
import os
import tempfile
import traceback
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter
import uvicorn
import logging
from mega_py import Mega   # ✅ use mega.pyx instead of mega.py

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set.")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is not set.")

WEBHOOK_PATH = "/webhook"

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer("👋 Hello! Send me a public Mega.nz link and I’ll download & upload it for you.")


@dp.message(F.text.startswith("https://mega.nz/") | F.text.startswith("https://mega.co.nz/"))
async def handle_mega(message: Message) -> None:
    link = message.text.strip()
    progress_msg = await message.answer("📥 Starting download…")

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            mega = Mega()
            await progress_msg.edit_text("📥 Connecting to Mega...")
            m = mega.login()  # ✅ anonymous login (no creds needed)

            await progress_msg.edit_text("📥 Getting file info...")
            file_info = m.get_public_url_info(link)
            if not file_info:
                await progress_msg.edit_text("❌ Could not retrieve file information. Is the link valid and public?")
                return

            file_name = file_info["name"]
            await progress_msg.edit_text(f"📥 Downloading <code>{file_name}</code>...")

            loop = asyncio.get_event_loop()
            file_path = await loop.run_in_executor(None, m.download_url, link, tmpdir)

            if file_path and os.path.exists(file_path):
                await progress_msg.edit_text("📤 Uploading file...")
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=FSInputFile(file_path),
                    caption=f"<code>{file_name}</code>",
                    disable_content_type_detection=True,
                )
                await progress_msg.delete()
            else:
                await progress_msg.edit_text("❌ Download failed or file not found.")
    except Exception as e:
        logger.error(f"Error processing Mega link: {e}\n{traceback.format_exc()}")
        try:
            await progress_msg.edit_text(f"❌ An error occurred: <code>{str(e)[:100]}...</code>")
        except:
            pass


app = FastAPI()


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.model_validate(data)
        asyncio.create_task(dp.feed_update(bot, update))
        return {"ok": True}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return {"ok": True}


@app.get("/kaithheathcheck")
async def healthcheck():
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup():
    logger.info("Starting up...")
    max_retries = 5
    retry_delay = 5
    for attempt in range(max_retries):
        try:
            webhook_url = WEBHOOK_URL + WEBHOOK_PATH
            logger.info(f"Setting webhook to {webhook_url}")
            await bot.set_webhook(webhook_url)
            logger.info("Webhook set successfully.")
            break
        except TelegramRetryAfter as e:
            logger.warning(
                f"Telegram API rate limit on set_webhook (attempt {attempt+1}): {e}. Waiting {e.retry_after}s."
            )
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error(f"Error setting webhook (attempt {attempt+1}): {e}", exc_info=True)
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                logger.error("Failed to set webhook after maximum retries.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down...")
    try:
        await bot.delete_webhook()
        logger.info("Webhook deleted.")
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
    try:
        await bot.session.close()
        logger.info("Bot session closed.")
    except Exception as e:
        logger.error(f"Error closing bot session: {e}")


if __name__ == "__main__":
    logger.info("Starting Uvicorn server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
