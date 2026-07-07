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

    trial_days: int = int(os.getenv("TRIAL_DAYS", "3"))
    referral_bonus_days: int = int(os.getenv("REFERRAL_BONUS_DAYS", "2"))

    plans: tuple = (
        ("1 месяц", 30, 99),
        ("3 месяца", 90, 249),
        ("6 месяцев", 180, 449),
        ("12 месяцев", 365, 799),
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
    for s in config.servers:
        if not s.panel_password:
            errors.append(f"Пароль панели для сервера '{s.name}' не задан")
        if "YOUR_SERVER_IP" in s.public_host:
            errors.append(f"Не заменен публичный адрес сервера '{s.name}'")
    if "YOUR_SERVER_IP" in config.sub_public_base_url:
        errors.append("SUB_PUBLIC_BASE_URL не настроен (адрес для subscription-ссылок)")
    return errors
