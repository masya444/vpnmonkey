"""
Сервер подписок (subscription server).

Это то, что даёт UX "как у HitVPN" без написания своего приложения: пользователь один раз
вставляет ссылку вида http://.../sub/<токен> в приложение (v2rayNG / Hiddify / Happ / v2Box),
и дальше приложение сам периодически подтягивает конфиг по этой ссылке — не нужно
каждый раз копировать новый ключ руками.

Запускается отдельным процессом (см. README) на том же VPS, где крутится бот,
либо вообще на том же сервере, где 3x-ui — не обязательно тот же процесс, что бот.
"""

import base64
import logging

from aiohttp import web

import database as db
import panel

log = logging.getLogger("subserver")


async def handle_subscription(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "")
    user = db.get_user_by_sub_token(token)

    # Намеренно одинаковый ответ на "нет такого токена" и "нет ключа" —
    # чтобы не давать подсказок тем, кто перебирает токены наугад.
    if not user or not user.get("vpn_uuid"):
        return web.Response(status=404, text="Not found")

    if not db.is_subscription_active(user["user_id"]):
        # Отдаем пустую подписку — приложение увидит 0 серверов, а не ошибку.
        # Это лучше, чем 403: не подсказывает наблюдателю, что аккаунт существует, но истек.
        return web.Response(text=base64.b64encode(b"").decode())

    link = panel.build_vless_link(user["vpn_uuid"], user["user_id"], user["server_name"])
    body = base64.b64encode(link.encode()).decode()
    return web.Response(text=body)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/sub/{token}", handle_subscription)
    app.router.add_get("/health", handle_health)
    return app


async def run_subscription_server(host: str, port: int):
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Subscription server started on %s:%s", host, port)
