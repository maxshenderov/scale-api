import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Session:
    """Сессия: связь 1С-формы с пользователем Telegram."""
    def __init__(self, session_id: str, chat_id: int, context: dict) -> None:
        self.session_id = session_id
        self.chat_id = chat_id
        self.context = context       # {form_name, object_data, type, ...}
        self.system_prompt = context.get("system_prompt", "")
        self.tools = context.get("tools", [])
        self.created = datetime.now(timezone.utc)
        self.last_question: str | None = None
        self.last_answer: str | None = None

    def store_answer(self, question: str, answer: str) -> None:
        self.last_question = question
        self.last_answer = answer


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}       # session_id → Session
        self._chat_sessions: dict[int, str] = {}      # chat_id → session_id (последняя активная)

    def create(self, session_id: str, chat_id: int, context: dict) -> Session:
        s = Session(session_id, chat_id, context)
        self._sessions[session_id] = s
        self._chat_sessions[chat_id] = session_id
        logger.info(f"Session created: {session_id} (chat_id={chat_id}, type={context.get('type')})")
        return s

    def get_by_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_by_chat(self, chat_id: int) -> Session | None:
        sid = self._chat_sessions.get(chat_id)
        if sid:
            return self._sessions.get(sid)
        return None

    def remove(self, session_id: str) -> None:
        s = self._sessions.pop(session_id, None)
        if s:
            self._chat_sessions.pop(s.chat_id, None)
            logger.info(f"Session removed: {session_id}")
