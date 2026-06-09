import os
import tempfile
import logging
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from llm import ask as llm_ask

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER = os.getenv("TELEGRAM_USER", "Макс")


class VoiceBot:
    def __init__(self, stt, ws_manager, sessions) -> None:
        if not TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
        self.stt = stt
        self.ws = ws_manager
        self.sessions = sessions
        self.bot = Bot(token=TOKEN)
        self.dp = Dispatcher()
        self.router = Router()
        self.router.message(F.voice)(self._handle_voice)
        self.router.message(F.text)(self._handle_text)
        self.dp.include_router(self.router)

    async def start(self) -> None:
        await self.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot started, polling Telegram...")
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        await self.dp.stop_polling()

    async def _handle_voice(self, msg: Message) -> None:
        voice = msg.voice
        file_info = await self.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            ogg_path = tmp.name
        try:
            await self.bot.download_file(file_info.file_path, ogg_path)
            await msg.reply("Распознаю...")
            text = self.stt.transcribe(ogg_path)
            if not text:
                await msg.reply("Не удалось распознать речь.")
                return
            await self._process(msg, text)
        finally:
            if os.path.exists(ogg_path):
                os.remove(ogg_path)

    async def _handle_text(self, msg: Message) -> None:
        await self._process(msg, msg.text)

    async def _process(self, msg: Message, text: str) -> None:
        chat_id = msg.chat.id

        # Сохраняем текст в ws_manager для 1С
        payload = {
            "type": "transcription",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": TELEGRAM_USER,
        }
        await self.ws.broadcast(payload)

        # Ищем сессию — нажата ли кнопка «Спросить» в 1С
        session = self.sessions.get_by_chat(chat_id)
        if session is None:
            await msg.reply(f"✓ {text}\n\n1С не подключена. Нажми «Спросить» на форме.")
            return

        # Есть сессия → отправляем в LLM
        await msg.reply(f"✓ {text}\n\nLLM думает...")
        try:
            answer = await llm_ask(
                prompt=text,
                system_prompt=session.system_prompt,
                tools=session.tools if session.tools else None,
            )
        except Exception as e:
            answer = f"Ошибка LLM: {e}"
            logger.error(f"LLM error: {e}")

        # Сохраняем ответ для 1С
        session.store_answer(text, answer)
        self.ws._latest = {
            "type": "ai_response",
            "text": answer,
            "question": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": TELEGRAM_USER,
        }

        # Отвечаем в Telegram
        await msg.reply(f"💬 {answer}")
