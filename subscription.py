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

import requests
from aiohttp import web

import database as db
import panel
from config import config

log = logging.getLogger("subserver")

MINIAPP_PATH = os.path.join(os.path.dirname(__file__), "miniapp.html")

_bot_username_cache = None


def send_telegram_message(chat_id: int, text: str):
    """Отправляет сообщение напрямую через HTTP API Telegram, без объекта Bot из aiogram.
    Нужно, потому что этот веб-сервер — отдельный от бота процесс/модуль."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException as e:
        log.error("Failed to send telegram message to %s: %s", chat_id, e)


def get_bot_username() -> str:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        resp = requests.get(f"https://api.telegram.org/bot{config.bot_token}/getMe", timeout=10)
        _bot_username_cache = resp.json()["result"]["username"]
    except Exception as e:
        log.error("Failed to fetch bot username: %s", e)
        _bot_username_cache = "unknown_bot"
    return _bot_username_cache


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
    используя токен из query-параметра ?token=, который бот вписывает в ссылку на кнопке).

    Если у пользователя еще нет ключа — создаем его прямо здесь, автоматически.
    Раньше это делала кнопка "Мой VPN" в боте, но её убрали (мини-аппка теперь
    единственное место, где показывается ключ), так что провижининг переехал сюда.
    """
    token = request.query.get("token", "")
    user = db.get_user_by_sub_token(token)
    if not user:
        return web.json_response({"error": "not_found"}, status=404)

    if not user.get("trial_used"):
        result = panel.create_vpn_client(user["user_id"])
        if result is not None:
            client_uuid, server_name = result
            db.save_vpn_client(user["user_id"], client_uuid, server_name)
            db.mark_trial_used(user["user_id"])
            db.extend_subscription(user["user_id"], config.trial_days)
            user = db.get_user_by_sub_token(token)  # перечитать свежие данные
        else:
            log.warning("Auto-provision failed for user_id=%s", user["user_id"])

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


async def handle_action_api(request: web.Request) -> web.Response:
    """Обрабатывает нажатия кнопок внутри мини-аппки (тарифы/реферал/история/поддержка).

    Мини-аппка, открытая через кнопку меню BotFather, НЕ может использовать
    Telegram.WebApp.sendData() — это ограничение Telegram (sendData работает только
    для Web App, открытых через кнопку клавиатуры). Поэтому вместо этого аппка
    напрямую стучится сюда через fetch, а сервер сам шлет сообщение в Telegram при надобности.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)

    token = data.get("token", "")
    action = data.get("action", "")
    user = db.get_user_by_sub_token(token)
    if not user:
        return web.json_response({"error": "not_found"}, status=404)

    user_id = user["user_id"]

    if action == "referral":
        bot_username = get_bot_username()
        link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        count = db.count_referrals(user_id)
        return web.json_response({"link": link, "count": count})

    if action == "history":
        until = user.get("subscription_until", 0)
        active = until > time.time()
        return web.json_response({"active": active, "subscription_until": until})

    if action == "support":
        send_telegram_message(user_id, "Напиши свой вопрос прямо в этот чат — я передам его в поддержку.")
        return web.json_response({"ok": True})

    if action == "buy_plan":
        days, price = int(data.get("days", 0)), int(data.get("price", 0))
        db.log_payment(user_id, price, days, status="pending")
        send_telegram_message(
            user_id,
            f"Тариф на {days} дней за {price}₽.\n\n"
            f"Пока оплата подключается вручную: напиши в поддержку с этим тарифом, "
            f"оплати переводом — и подписка активируется в течение нескольких минут.",
        )
        if config.admin_id:
            send_telegram_message(
                config.admin_id,
                f"Заявка на оплату из мини-аппки: user_id={user_id}, "
                f"тариф {days} дней за {price}₽. Подтвердить: /confirm {user_id} {days} {price}",
            )
        return web.json_response({"ok": True})

    return web.json_response({"error": "unknown_action"}, status=400)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/sub/{token}", handle_subscription)
    app.router.add_get("/app", handle_miniapp)
    app.router.add_get("/api/status", handle_status_api)
    app.router.add_post("/api/action", handle_action_api)
    app.router.add_get("/health", handle_health)
    return app


async def run_subscription_server(host: str, port: int):
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Subscription server started on %s:%s", host, port)
