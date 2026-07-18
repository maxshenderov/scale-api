import httpx
from config import settings


class WmsClient:
    """HTTP-клиент к 1С Liko_Rest. Работает через активное подключение из БД."""

    def __init__(self, base_url: str = "", login: str = "", password: str = ""):
        self.base_url = base_url
        self.login = login
        self.password = password

    async def call(self, proc_name: str, **params) -> dict:
        body = {"ProcName": proc_name, **params}
        auth = (self.login, self.password) if self.login else None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self.base_url, json=body, auth=auth)
            resp.raise_for_status()
            return resp.json()

    @classmethod
    def from_connection(cls, conn: dict) -> "WmsClient":
        return cls(
            base_url=conn.get("url", ""),
            login=conn.get("login", ""),
            password=conn.get("password", ""),
        )
