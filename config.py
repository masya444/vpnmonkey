"""
Конфигурация VPN Monkey.

ВАЖНО ПО БЕЗОПАСНОСТИ:
- Все секреты — только из переменных окружения. Никогда не хардкодь токены/пароли в код.
- .env в .gitignore, никогда не пушить в открытый GitHub.
"""

import os
from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    name: str
    panel_url: str
    panel_username: str
    panel_password: str
    inbound_id: int
    public_host: str  # IP или домен, который увидит клиент в конфиге
    public_port: int = 21309  # порт inbound-а (тот, что видно в панели рядом с inbound-ом)


@dataclass
class Config:
    app_name: str = "VPN Monkey"

    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_id: int = int(os.getenv("ADMIN_ID", "0"))
    db_path: str = os.getenv("DB_PATH", "vpnmonkey.db")

    # Веб-сервер, который отдает subscription-ссылки приложениям (v2rayNG/Hiddify/Happ)
    sub_server_host: str = os.getenv("SUB_SERVER_HOST", "0.0.0.0")
    sub_server_port: int = int(os.getenv("SUB_SERVER_PORT", "8080"))
    sub_public_base_url: str = os.getenv("SUB_PUBLIC_BASE_URL", "http://YOUR_SERVER_IP:8080")

    # Режим работы бота: webhook (для бесплатных веб-хостингов типа Render, которые дают
    # публичный HTTP-адрес, но не дают держать вечный фоновый процесс) или polling
    # (для VPS/Railway, где можно просто гонять бота в фоне).
    use_webhook: bool = os.getenv("USE_WEBHOOK", "false").lower() == "true"
    webhook_path: str = os.getenv("WEBHOOK_PATH", "/webhook")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    # Render и подобные платформы сами подставляют порт через переменную PORT —
    # если она есть, используем её вместо SUB_SERVER_PORT.
    port: int = int(os.getenv("PORT", os.getenv("SUB_SERVER_PORT", "8080")))

    trial_days: int = int(os.getenv("TRIAL_DAYS", "3"))
    referral_bonus_days: int = int(os.getenv("REFERRAL_BONUS_DAYS", "2"))
    max_devices: int = int(os.getenv("MAX_DEVICES", "3"))

    # Временный тестовый режим: True = бот не стучится в реальную VPN-панель,
    # а выдает фейковый ключ. Нужен, чтобы проверить логику бота (меню, триал,
    # рефералку, тарифы) до того, как появится настоящий VPN-сервер.
    # Когда сервер будет готов — просто поставь FAKE_VPN_SERVER=false (или убери переменную).
    fake_vpn_server: bool = os.getenv("FAKE_VPN_SERVER", "true").lower() == "true"

    plans: tuple = (
        ("1 месяц", 30, 189),
        ("3 месяца", 90, 479),
        ("6 месяцев", 180, 790),
        ("12 месяцев", 365, 1290),
    )

    servers: list = field(default_factory=lambda: [
        ServerConfig(
            name=os.getenv("SERVER1_NAME", "Server-NL-1"),
            panel_url=os.getenv("SERVER1_PANEL_URL", "https://your-server-ip:2053"),
            panel_username=os.getenv("SERVER1_PANEL_USER", "admin"),
            panel_password=os.getenv("SERVER1_PANEL_PASS", ""),
            inbound_id=int(os.getenv("SERVER1_INBOUND_ID", "1")),
            public_host=os.getenv("SERVER1_PUBLIC_HOST", "your-server-ip"),
            public_port=int(os.getenv("SERVER1_PUBLIC_PORT", "21309")),
        ),
    ])


config = Config()


def validate_config() -> list[str]:
    errors = []
    if not config.bot_token:
        errors.append("BOT_TOKEN не задан")
    if config.admin_id == 0:
        errors.append("ADMIN_ID не задан (узнать свой ID у @userinfobot)")

    # В тестовом режиме (без реального VPN-сервера) эти проверки пропускаем —
    # они всё равно не заполнены, и это ожидаемо на этом этапе.
    if not config.fake_vpn_server:
        for s in config.servers:
            if not s.panel_password:
                errors.append(f"Пароль панели для сервера '{s.name}' не задан")
            if "YOUR_SERVER_IP" in s.public_host:
                errors.append(f"Не заменен публичный адрес сервера '{s.name}'")
        if "YOUR_SERVER_IP" in config.sub_public_base_url:
            errors.append("SUB_PUBLIC_BASE_URL не настроен (адрес для subscription-ссылок)")
    return errors
