import logging
import secrets
from pathlib import Path

import discord
from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .auth import Auth, AuthError
from .views import apply_form, build_context, fetch_invite_options

log = logging.getLogger("rolebot.web")
BASE_DIR = Path(__file__).resolve().parent


def _renderer() -> Environment:
    return Environment(
        loader=FileSystemLoader(BASE_DIR / "templates"),
        autoescape=select_autoescape(["html"]),
        enable_async=False,
    )


def _render(request: web.Request, template: str, **context) -> web.Response:
    html = request.app["jinja"].get_template(template).render(**context)
    return web.Response(text=html, content_type="text/html")


def _flash(session: dict, message: str | None = None) -> str | None:
    if message is not None:
        session["flash"] = message
        return None
    return session.pop("flash", None)


def _user_guilds(session: dict) -> list[tuple[str, str]]:
    return sorted(session.get("guilds", {}).items(), key=lambda item: item[1].lower())


def _accessible_guild(request: web.Request) -> tuple[dict, discord.Guild]:
    session = request.app["auth"].current_session(request)
    guild_id = request.match_info["guild_id"]
    guild = request.app["bot"].get_guild(int(guild_id)) if guild_id.isdigit() else None
    if session is None or guild is None or guild_id not in session.get("guilds", {}):
        raise web.HTTPForbidden(text="You don't have access to that server.")
    return session, guild


async def index(request: web.Request) -> web.Response:
    session = request.app["auth"].current_session(request)
    if session is None or "user" not in session:
        return _render(request, "index.html", user=None, guilds=[], login_url="/login")
    return _render(
        request,
        "index.html",
        user=session["user"],
        guilds=_user_guilds(session),
        flash=_flash(session),
        login_url="/login",
    )


async def login(request: web.Request) -> web.Response:
    auth: Auth = request.app["auth"]
    sid = auth.sessions.create({"state": secrets.token_urlsafe(16), "csrf": secrets.token_urlsafe(16)})
    response = web.HTTPFound(auth.authorize_url(auth.sessions.get(sid)["state"]))
    auth.set_cookie(response, sid)
    raise response


async def callback(request: web.Request) -> web.Response:
    auth: Auth = request.app["auth"]
    session = auth.current_session(request)
    state = request.query.get("state")
    code = request.query.get("code")
    if session is None or not code or not state or session.get("state") != state:
        raise web.HTTPBadRequest(text="Invalid login state. Please try again.")
    try:
        token = await auth.exchange_code(code)
        user = await auth.fetch_user(token)
        guilds = await auth.fetch_guilds(token)
    except AuthError as error:
        log.warning("OAuth callback failed: %s", error)
        raise web.HTTPBadGateway(text="Discord login failed. Please try again.")
    session.pop("state", None)
    session["user"] = {
        "id": str(user["id"]),
        "name": user.get("global_name") or user.get("username") or "user",
    }
    session["guilds"] = auth.manageable_guilds(guilds)
    raise web.HTTPFound("/")


async def logout(request: web.Request) -> web.Response:
    auth: Auth = request.app["auth"]
    auth.sessions.destroy(auth.session_id(request))
    response = web.HTTPFound("/")
    auth.clear_cookie(response)
    raise response


async def guild_settings(request: web.Request) -> web.Response:
    session, guild = _accessible_guild(request)
    context = build_context(request.app["bot"], guild)
    invite_options = await fetch_invite_options(guild)
    context.update(
        user=session.get("user"),
        guilds=_user_guilds(session),
        csrf=session.get("csrf"),
        flash=_flash(session),
        invite_options=invite_options,
        invite_codes={option["code"] for option in invite_options},
    )
    return _render(request, "guild.html", **context)


async def guild_settings_post(request: web.Request) -> web.Response:
    session, guild = _accessible_guild(request)
    bot = request.app["bot"]
    form = await request.post()
    if not session.get("csrf") or form.get("csrf") != session["csrf"]:
        raise web.HTTPForbidden(text="Invalid form token. Reload the page and try again.")

    errors = apply_form(bot, guild, form)

    notes: list[str] = []
    cog = bot.get_cog("ReactionRoles")
    if cog is not None:
        cog.rebuild_views()
        if form.get("refresh_menus") == "on":
            posted, failed = await cog.refresh_all(guild)
            notes.append(f"refreshed {posted} menu(s), {failed} failed")
        else:
            menus = bot.guild_config(guild.id).get("role_menus", [])
            updated = 0
            for menu in menus:
                if menu.get("message_id"):
                    ok, _ = await cog.refresh_menu(menu)
                    updated += int(ok)
            if updated:
                notes.append(f"updated {updated} posted menu(s)")

    if errors:
        _flash(session, "Some values were not saved: " + " ".join(errors))
    else:
        _flash(session, "Saved." + (f" Also {'; '.join(notes)}." if notes else ""))
    raise web.HTTPFound(f"/guild/{guild.id}")


async def _cleanup(app: web.Application) -> None:
    await app["auth"].close()


def create_app(bot, *, client_id: str, client_secret: str, base_url: str, secret: str | None) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app["jinja"] = _renderer()
    app["auth"] = Auth(bot, client_id=client_id, client_secret=client_secret, base_url=base_url, secret=secret)

    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_get("/callback", callback)
    app.router.add_route("*", "/logout", logout)
    app.router.add_get("/guild/{guild_id}", guild_settings)
    app.router.add_post("/guild/{guild_id}", guild_settings_post)
    app.router.add_static("/static", BASE_DIR / "static")
    app.on_startup.append(lambda app: app["auth"].start())
    app.on_cleanup.append(_cleanup)
    return app
