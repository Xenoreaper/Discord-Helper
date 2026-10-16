import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

from aiohttp import ClientSession, web

SESSION_COOKIE = "rolebot_session"
SESSION_TTL = 7 * 24 * 60 * 60
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
API_BASE = "https://discord.com/api/v10"
IDENTIFY_SCOPE = "identify guilds"
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8


class AuthError(Exception):
    """Raised when the Discord OAuth flow fails."""


class SessionStore:
    """In-memory sessions keyed by a signed, random cookie value."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()
        self._sessions: dict[str, dict] = {}

    def create(self, data: dict) -> str:
        sid = secrets.token_urlsafe(24)
        data["expires"] = time.time() + SESSION_TTL
        self._sessions[sid] = data
        return sid

    def get(self, sid: str | None) -> dict | None:
        session = self._sessions.get(sid) if sid else None
        if session is None:
            return None
        if session["expires"] < time.time():
            self._sessions.pop(sid, None)
            return None
        return session

    def destroy(self, sid: str | None) -> None:
        if sid:
            self._sessions.pop(sid, None)

    def sign(self, sid: str) -> str:
        mac = hmac.new(self._secret, sid.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(mac).decode().rstrip("=")
        return f"{sid}.{signature}"

    def unsign(self, value: str | None) -> str | None:
        if not value:
            return None
        sid, _, signature = value.rpartition(".")
        if not sid or not signature:
            return None
        expected = hmac.new(self._secret, sid.encode(), hashlib.sha256).digest()
        try:
            given = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        except ValueError:
            return None
        return sid if hmac.compare_digest(expected, given) else None


class Auth:
    def __init__(self, bot, *, client_id: str, client_secret: str, base_url: str, secret: str | None) -> None:
        self.bot = bot
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.sessions = SessionStore(secret or secrets.token_hex(32))
        self.http: ClientSession | None = None

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/callback"

    @property
    def _secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    async def start(self) -> None:
        self.http = ClientSession()

    async def close(self) -> None:
        if self.http is not None:
            await self.http.close()

    def session_id(self, request: web.Request) -> str | None:
        return self.sessions.unsign(request.cookies.get(SESSION_COOKIE))

    def current_session(self, request: web.Request) -> dict | None:
        return self.sessions.get(self.session_id(request))

    def set_cookie(self, response: web.Response, sid: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            self.sessions.sign(sid),
            max_age=SESSION_TTL,
            httponly=True,
            samesite="Lax",
            secure=self._secure_cookies,
            path="/",
        )

    def clear_cookie(self, response: web.Response) -> None:
        response.del_cookie(SESSION_COOKIE, path="/")

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": IDENTIFY_SCOPE,
            "state": state,
            "prompt": "consent",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> str:
        if self.http is None:
            raise AuthError("HTTP client not started")
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        async with self.http.post(TOKEN_URL, data=data) as response:
            if response.status != 200:
                raise AuthError(f"token exchange failed ({response.status})")
            payload = await response.json()
        token = payload.get("access_token")
        if not token:
            raise AuthError("no access token in response")
        return token

    async def _get(self, path: str, token: str) -> list | dict:
        if self.http is None:
            raise AuthError("HTTP client not started")
        async with self.http.get(f"{API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status != 200:
                raise AuthError(f"Discord API request to {path} failed ({response.status})")
            return await response.json()

    async def fetch_user(self, token: str) -> dict:
        return await self._get("/users/@me", token)

    async def fetch_guilds(self, token: str) -> list[dict]:
        return await self._get("/users/@me/guilds", token)

    def manageable_guilds(self, guilds: list[dict]) -> dict[str, str]:
        accessible: dict[str, str] = {}
        for guild in guilds:
            permissions = int(guild.get("permissions", 0))
            if not guild.get("owner") and not permissions & (MANAGE_GUILD | ADMINISTRATOR):
                continue
            guild_id = str(guild["id"])
            if self.bot.get_guild(int(guild_id)) is None:
                continue
            accessible[guild_id] = guild["name"]
        return accessible
