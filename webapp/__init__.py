import logging
import os
from urllib.parse import urlparse

from aiohttp import web

log = logging.getLogger("rolebot.web")


def _web_enabled() -> bool:
    return os.getenv("WEB_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _check_redirect_uri(base_url: str) -> str:
    redirect_uri = f"{base_url.rstrip('/')}/callback"
    parsed = urlparse(base_url)
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        log.warning(
            "WEB_BASE_URL uses http:// but Discord requires https:// for non-loopback redirect URIs. "
            "The current redirect URI will be rejected with 'Invalid OAuth2 redirect_uri'."
        )
    log.info(
        "Discord OAuth redirect URI: %s — add this exact URL to your app's OAuth2 redirects.",
        redirect_uri,
    )
    return redirect_uri


async def start_webapp(bot) -> web.AppRunner | None:
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    if not _web_enabled():
        return None
    if not (client_id and client_secret):
        log.info("Web UI disabled — set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET to enable it.")
        return None

    from .app import create_app

    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8080"))
    base_url = os.getenv("WEB_BASE_URL", f"http://localhost:{port}")
    _check_redirect_uri(base_url)
    app = create_app(
        bot,
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        secret=os.getenv("WEB_SESSION_SECRET") or None,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("Web configuration UI listening on %s:%s (base URL %s)", host, port, base_url)
    return runner


async def stop_webapp(runner: web.AppRunner | None) -> None:
    if runner is not None:
        await runner.cleanup()
