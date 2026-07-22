"""
VPN Monkey — Telegram-бот для продажи VPN-доступа.

Что нового по сравнению с черновиком:
- Подписка выдается как subscription-ссылка (для v2rayNG/Hiddify/Happ), а не разовый ключ —
  это и есть "как у HitVPN", только через стандартные приложения, а не самописное.
- Логи пишутся в файл и в консоль — при падении сможешь посмотреть, что случилось.
- Если панель VPN-сервера недоступна — бот не роняет весь процесс, а сообщает пользователю
  и админу, вместо того чтобы зависнуть или выдать пустой ключ.
- Админ-команды: /stats (сводка), /confirm (ручное подтверждение оплаты на первое время,
  пока не подключена автоматическая оплата).

Запуск:
    pip install -r requirements.txt --break-system-packages
    export BOT_TOKEN=... ADMIN_ID=... SERVER1_PANEL_PASS=... SERVER1_PUBLIC_HOST=... SUB_PUBLIC_BASE_URL=...
    python main.py
"""

import asyncio
import logging
import time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import config, validate_config
import database as db
import panel
from subscription import run_subscription_server, create_app as create_sub_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("vpnmonkey.log"), logging.StreamHandler()],
)
log = logging.getLogger("bot")

bot = Bot(token=config.bot_token)
dp = Dispatcher()


def main_menu_kb():
    """Главное меню — расположение один-в-один как у Utka VPN:
    каждая кнопка в своей строке, кроме последней пары."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Подключить VPN 🚀", callback_data="connect_vpn", style="success")
    kb.button(text="🔑 Моя подписка", callback_data="my_subscription")
    kb.button(text="🎁 Пригласить и получить дни", callback_data="referral")
    kb.button(text="🆘 Поддержка", callback_data="support")
    kb.button(text="ℹ️ О сервисе", callback_data="about")
    kb.adjust(1, 1, 1, 2)
    return kb.as_markup()


def webapp_url_for(sub_token: str) -> str:
    return f"{config.sub_public_base_url}/app?token={sub_token}"


def subscription_kb(sub_token: str):
    """Клавиатура на экране 'Моя подписка': если есть мини-аппа по HTTPS — открыть её,
    иначе обычная ссылка (Telegram требует HTTPS именно для web_app-кнопок)."""
    kb = InlineKeyboardBuilder()
    app_url = webapp_url_for(sub_token)
    if app_url.startswith("https://"):
        kb.button(text="🐒 Открыть Monkey VPN", web_app=WebAppInfo(url=app_url))
    else:
        kb.button(text="🐒 Открыть Monkey VPN", url=app_url)
    kb.button(text="⬅️ Назад", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()


def back_kb():
    """Простая клавиатура с одной кнопкой назад — используется во всех подменю,
    чтобы не дублировать полное меню там, где оно не нужно."""
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="back_to_menu")
    return kb.as_markup()


def plans_kb():
    kb = InlineKeyboardBuilder()
    for name, days, price in config.plans:
        kb.button(text=f"{name} — {price}₽", callback_data=f"buy_{days}_{price}")
    kb.button(text="⬅️ Назад", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()


def format_time_left(unix_ts: int) -> str:
    if unix_ts <= time.time():
        return "не активна"
    days_left = int((unix_ts - time.time()) // 86400) + 1
    return f"осталось {days_left} дн."


def sub_link_for(user: dict) -> str:
    return f"{config.sub_public_base_url}/sub/{user['sub_token']}"


async def notify_admin(text: str):
    if config.admin_id:
        try:
            await bot.send_message(config.admin_id, f"⚠️ {text}")
        except Exception:
            log.exception("Failed to notify admin")


@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    referred_by = None
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1].removeprefix("ref_"))
            if ref_id != user_id:
                referred_by = ref_id
        except ValueError:
            pass

    db.get_or_create_user(user_id, username, referred_by)

    if referred_by and not db.referral_bonus_already_given(referred_by, user_id):
        db.extend_subscription(referred_by, config.referral_bonus_days)
        db.mark_referral_bonus_given(referred_by, user_id)
        try:
            await bot.send_message(
                referred_by,
                f"🎉 По твоей ссылке зашел новый пользователь! "
                f"Тебе начислено +{config.referral_bonus_days} дня подписки.",
            )
        except Exception:
            pass

    user = db.get_user(user_id) or db.get_or_create_user(user_id, "user")

    text = (
        f"Привет! Я — {config.app_name} 🐵\n\n"
        f"Подключи свой VPN здесь.\n\n"
        f"⚡ Быстрое подключение\n"
        f"🛡️ Максимальная защита\n"
        f"🌍 Доступ ко всем сайтам"
    )

    await message.answer(text, reply_markup=main_menu_kb())


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery):
    await call.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await call.answer()


@dp.callback_query(F.data == "connect_vpn")
async def connect_vpn(call: CallbackQuery):
    """Кнопка 'Подключить VPN': первый раз — выдаёт бесплатный триал,
    дальше — ведёт к выбору платного тарифа (как у Utka VPN)."""
    user_id = call.from_user.id
    user = db.get_user(user_id) or db.get_or_create_user(user_id, call.from_user.username or call.from_user.full_name)

    if not user["trial_used"]:
        await call.message.edit_text("Создаю тебе доступ, секунду...")
        result = panel.create_vpn_client(user_id)
        if result is None:
            await call.message.edit_text(
                "Не получилось создать доступ — сервер временно недоступен. "
                "Попробуй через пару минут, я уже знаю о проблеме.",
                reply_markup=back_kb(),
            )
            await notify_admin(f"create_vpn_client вернул None для user_id={user_id}")
            return

        client_uuid, server_name = result
        db.save_vpn_client(user_id, client_uuid, server_name)
        db.mark_trial_used(user_id)
        db.extend_subscription(user_id, config.trial_days)
        user = db.get_user(user_id) or db.get_or_create_user(user_id, "user")

        await call.message.edit_text(
            f"Готово! Пробный период — {config.trial_days} дня, до {config.max_devices} устройств.\n\n"
            f"Твоя ссылка подписки (вставляется один раз):\n"
            f"`{sub_link_for(user)}`\n\n"
            f"Как подключиться:\n"
            f"1️⃣ Установи приложение — Android: v2rayNG, iPhone/Mac: Hiddify или Happ, Windows: Hiddify\n"
            f"2️⃣ В приложении выбери «Добавить подписку по ссылке» и вставь ссылку выше",
            parse_mode="Markdown",
            reply_markup=subscription_kb(user["sub_token"]),
        )
        return

    await call.message.edit_text(
        f"Любой тариф включает до {config.max_devices} устройств.\n\nВыбери срок подписки:",
        reply_markup=plans_kb(),
    )


@dp.callback_query(F.data == "my_subscription")
async def my_subscription(call: CallbackQuery):
    """Кнопка 'Моя подписка': только статус, ничего не активирует."""
    user_id = call.from_user.id
    user = db.get_user(user_id) or db.get_or_create_user(user_id, call.from_user.username or call.from_user.full_name)

    if not user["trial_used"] or not db.is_subscription_active(user_id):
        await call.message.edit_text(
            "❌ Нет активной подписки.\n\n"
            + (
                f"Подключи бесплатные {config.trial_days} дня или выбери тариф."
                if not user["trial_used"]
                else "Подписка закончилась. Выбери тариф, чтобы продолжить."
            ),
            reply_markup=InlineKeyboardBuilder()
            .button(text="🚀 Подключить VPN", callback_data="connect_vpn")
            .button(text="⬅️ Назад", callback_data="back_to_menu")
            .adjust(1)
            .as_markup(),
        )
        return

    await call.message.edit_text(
        f"Твоя ссылка подписки:\n`{sub_link_for(user)}`\n\n"
        f"Статус: {format_time_left(user['subscription_until'])}.",
        parse_mode="Markdown",
        reply_markup=subscription_kb(user["sub_token"]),
    )


@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    text = (
        f"ℹ️ О сервисе {config.app_name}\n\n"
        f"🐒 Быстрое подключение — доступ выдаётся за пару минут\n"
        f"🔒 Защищённое соединение без просадок скорости\n"
        f"🌍 Доступ к нужным сайтам и сервисам\n"
        f"📱 До {config.max_devices} устройств на одной подписке\n"
        f"🔓 Честные условия — без скрытых списаний"
    )
    await call.message.edit_text(text, reply_markup=back_kb())


@dp.callback_query(F.data == "how_to")
async def how_to(call: CallbackQuery):
    user = db.get_user(call.from_user.id) or db.get_or_create_user(call.from_user.id, "user")
    link = sub_link_for(user)
    text = (
        "Подключение за 2 шага:\n\n"
        "1️⃣ Установи приложение:\n"
        "• Android — v2rayNG\n"
        "• iPhone/Mac — Hiddify или Happ\n"
        "• Windows — Hiddify\n\n"
        f"2️⃣ В приложении выбери «Добавить подписку по ссылке» и вставь:\n`{link}`\n\n"
        "После этого просто нажимай «Подключить» в приложении — обновлять ссылку вручную "
        "не нужно, она обновляется сама."
    )
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb())


@dp.callback_query(F.data == "plans")
async def plans(call: CallbackQuery):
    await call.message.edit_text("Выбери тариф:", reply_markup=plans_kb())


@dp.callback_query(F.data.startswith("buy_"))
async def buy(call: CallbackQuery):
    _, days, price = call.data.split("_")
    db.log_payment(call.from_user.id, int(price), int(days), status="pending")
    user = db.get_user(call.from_user.id) or db.get_or_create_user(call.from_user.id, "user")
    # TODO: реальная интеграция с ЮKassa/CryptoBot вместо ручного подтверждения.
    await call.message.edit_text(
        f"Тариф на {days} дней за {price}₽.\n\n"
        f"Пока оплата подключается вручную: напиши в поддержку с этим тарифом, "
        f"оплати переводом — и подписка активируется в течение нескольких минут.",
        reply_markup=back_kb(),
    )
    await notify_admin(
        f"Новая заявка на оплату: user_id={call.from_user.id}, "
        f"тариф {days} дней за {price}₽. Подтвердить: /confirm {call.from_user.id} {days} {price}"
    )


@dp.callback_query(F.data == "referral")
async def referral(call: CallbackQuery):
    user_id = call.from_user.id
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    count = db.count_referrals(user_id)
    earned_days = count * config.referral_bonus_days
    await call.message.edit_text(
        f"🎁 Приглашай друзей с {config.app_name}\n\n"
        f"Получай +{config.referral_bonus_days} дня подписки за каждого друга, "
        f"который зайдёт по твоей ссылке.\n\n"
        f"Друзей приглашено: {count}\n"
        f"Дней начислено: {earned_days}\n"
        f"Твой ID: {user_id}\n\n"
        f"Твоя ссылка:\n{link}",
        reply_markup=back_kb(),
    )


@dp.callback_query(F.data == "support")
async def support(call: CallbackQuery):
    user = db.get_user(call.from_user.id) or db.get_or_create_user(call.from_user.id, "user")
    await call.message.edit_text(
        "Напиши свой вопрос прямо в этот чат — я передам его в поддержку.",
        reply_markup=back_kb(),
    )


# --- Данные из мини-аппки ---

@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Мини-аппка шлет сюда события через Telegram.WebApp.sendData() (см. miniapp.html)."""
    import json as _json
    try:
        payload = _json.loads(message.web_app_data.data)
    except Exception:
        return

    action = payload.get("action")
    user_id = message.from_user.id

    if action == "buy_plan":
        days, price = payload.get("days"), payload.get("price")
        db.log_payment(user_id, int(price), int(days), status="pending")
        await message.answer(
            f"Тариф на {days} дней за {price}₽.\n\n"
            f"Пока оплата подключается вручную: напиши в поддержку с этим тарифом, "
            f"оплати переводом — и подписка активируется в течение нескольких минут."
        )
        await notify_admin(
            f"Заявка на оплату из мини-аппки: user_id={user_id}, "
            f"тариф {days} дней за {price}₽. Подтвердить: /confirm {user_id} {days} {price}"
        )
    elif action == "referral":
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
        count = db.count_referrals(user_id)
        await message.answer(f"Твоя реферальная ссылка:\n{link}\n\nУже пригласил: {count} чел.")
    elif action == "history":
        s = db.get_stats()
        user = db.get_user(user_id) or db.get_or_create_user(user_id, "user")
        await message.answer(f"Статус подписки: {format_time_left(user['subscription_until'])}.")
    elif action == "support":
        await message.answer("Напиши свой вопрос прямо в этот чат — я передам его в поддержку.")


# --- Админ-команды ---

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.admin_id:
        return
    s = db.get_stats()
    await message.answer(
        f"📊 Статистика {config.app_name}\n\n"
        f"Всего пользователей: {s['total_users']}\n"
        f"Активных подписок: {s['active_subs']}\n"
        f"Подтвержденная выручка: {s['total_revenue']}₽"
    )


@dp.message(Command("confirm"))
async def cmd_confirm(message: Message):
    """Ручное подтверждение оплаты: /confirm <user_id> <days> <amount>"""
    if message.from_user.id != config.admin_id:
        return
    try:
        _, user_id_s, days_s, amount_s = message.text.split()
        user_id, days, amount = int(user_id_s), int(days_s), int(amount_s)
    except ValueError:
        await message.answer("Формат: /confirm <user_id> <дни> <сумма>")
        return

    new_until = db.extend_subscription(user_id, days)
    db.log_payment(user_id, amount, days, status="confirmed")
    await message.answer(f"Готово. Подписка user_id={user_id} продлена до {time.ctime(new_until)}.")
    try:
        await bot.send_message(user_id, f"✅ Оплата получена! Подписка продлена на {days} дней.")
    except Exception:
        log.warning("Could not notify user %s about confirmed payment", user_id)


async def main():
    errors = validate_config()
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        print("Бот не запущен — исправь ошибки конфигурации выше (переменные окружения).")
        return

    db.init_db()
    log.info("%s bot starting (webhook=%s)", config.app_name, config.use_webhook)

    if config.use_webhook:
        # Режим для бесплатных хостингов вроде Render: один общий aiohttp-сервер
        # одновременно обслуживает и подписки/мини-аппку (subscription.py), и вебхук бота —
        # Telegram сам стучится сюда при каждом новом сообщении, никакого фонового
        # процесса "опроса" (polling) не требуется, поэтому это работает на обычном
        # бесплатном веб-сервисе.
        webhook_url = f"{config.sub_public_base_url}{config.webhook_path}"
        await bot.set_webhook(
            webhook_url,
            secret_token=config.webhook_secret or None,
            drop_pending_updates=True,
        )
        log.info("Webhook set to %s", webhook_url)

        app = create_sub_app()
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=config.webhook_secret or None,
        ).register(app, path=config.webhook_path)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.port)
        await site.start()
        log.info("Webhook server listening on 0.0.0.0:%s", config.port)

        # Держим процесс живым вечно (aiohttp сам обрабатывает запросы в фоне).
        await asyncio.Event().wait()
    else:
        # Обычный режим для VPS/Railway — бот сам постоянно спрашивает Telegram
        # о новых сообщениях (polling), плюс отдельно поднят сервер подписок/мини-аппки.
        await bot.delete_webhook(drop_pending_updates=True)
        await asyncio.gather(
            dp.start_polling(bot),
            run_subscription_server(config.sub_server_host, config.port),
        )


if __name__ == "__main__":
    asyncio.run(main())
