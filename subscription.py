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
import json
import logging
import os
import time

from aiohttp import web

import database as db
import panel
from config import config

log = logging.getLogger("subserver")

MINIAPP_PATH = os.path.join(os.path.dirname(__file__), "miniapp.html")


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


async def handle_miniapp(request: web.Request) -> web.Response:
    """Отдает саму страницу Mini App. Telegram открывает её внутри своего интерфейса."""
    with open(MINIAPP_PATH, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def handle_status_api(request: web.Request) -> web.Response:
    """JSON-статус для конкретного пользователя (мини-аппка дергает это через fetch,
    используя токен из query-параметра ?token=, который бот вписывает в ссылку на кнопке)."""
    token = request.query.get("token", "")
    user = db.get_user_by_sub_token(token)
    if not user:
        return web.json_response({"error": "not_found"}, status=404)

    until = user.get("subscription_until", 0)
    days_left = max(0, int((until - time.time()) // 86400) + (1 if until > time.time() else 0))
    active = until > time.time()

    return web.json_response({
        "days_left": days_left,
        "active": active,
        "trial_used": bool(user.get("trial_used")),
        "sub_link": f"{config.sub_public_base_url}/sub/{token}",
        "referral_count": db.count_referrals(user["user_id"]),
    })


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/sub/{token}", handle_subscription)
    app.router.add_get("/app", handle_miniapp)
    app.router.add_get("/api/status", handle_status_api)
    app.router.add_get("/health", handle_health)
    return app


async def run_subscription_server(host: str, port: int):
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Subscription server started on %s:%s", host, port)
