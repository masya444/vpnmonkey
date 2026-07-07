"""
Клиент для панели(ей) 3x-ui — управляет VPN-серверами через их API.

Настройка ДО запуска бота:
1. Поднять VPS (Нидерланды/Германия/Финляндия — от 5-10$/мес).
2. Установить 3x-ui: https://github.com/MHSanaei/3x-ui (одна команда из их README).
3. Создать inbound с протоколом VLESS + Reality (лучший баланс скорости/скрытности сейчас).
4. Прописать данные сервера в .env (см. config.py — SERVER1_*).

БЕЗОПАСНОСТЬ СЕРВЕРА (чтобы не взломали):
- Смени порт панели со стандартного на случайный.
- Включи 2FA в 3x-ui, если версия поддерживает.
- Файрвол (ufw): порт панели открыт только для твоего IP, не для всех.
- Обновляй 3x-ui регулярно — в старых версиях находили уязвимости.
- Пароль от панели не хранится в коде и не появляется в логах (см. ниже).
- Бэкапь x-ui.db раз в неделю в другое место (не только на сам сервер).
"""

import logging
import uuid

import requests
from requests.adapters import HTTPAdapter, Retry

from config import config, ServerConfig

log = logging.getLogger("panel")

# Отдельная сессия на каждый сервер — токены логина у 3x-ui не шарятся между серверами.
_sessions: dict[str, requests.Session] = {}


def _get_session(server: ServerConfig) -> requests.Session:
    if server.name not in _sessions:
        s = requests.Session()
        s.verify = False  # самоподписанный сертификат на своем VPS — нормально;
                           # если поставишь Let's Encrypt, поставь verify=True
        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        s.mount("https://", HTTPAdapter(max_retries=retries))
        s.mount("http://", HTTPAdapter(max_retries=retries))
        _sessions[server.name] = s
    return _sessions[server.name]


def _login(server: ServerConfig) -> bool:
    session = _get_session(server)
    try:
        resp = session.post(
            f"{server.panel_url}/login",
            data={"username": server.panel_username, "password": server.panel_password},
            timeout=10,
        )
        ok = resp.ok and resp.json().get("success", False)
        if not ok:
            log.warning("Login failed for server %s (status %s)", server.name, resp.status_code)
        return ok
    except requests.RequestException as e:
        log.error("Cannot reach panel for server %s: %s", server.name, e)
        return False


def pick_server() -> ServerConfig:
    """Пока просто первый сервер из списка. Когда серверов станет больше одного —
    замени на реальный выбор по нагрузке (например, храни в БД счетчик клиентов на сервер
    и бери тот, где их меньше)."""
    return config.servers[0]


def create_vpn_client(user_id: int) -> tuple[str, str] | None:
    """Создает клиента на выбранном сервере.
    Возвращает (client_uuid, server_name) или None при ошибке."""
    server = pick_server()
    if not _login(server):
        return None

    client_uuid = str(uuid.uuid4())
    email = f"user_{user_id}"

    payload = {
        "id": server.inbound_id,
        "settings": (
            '{"clients": [{"id": "%s", "email": "%s", "enable": true, "flow": ""}]}'
            % (client_uuid, email)
        ),
    }

    session = _get_session(server)
    try:
        resp = session.post(f"{server.panel_url}/panel/api/inbounds/addClient", json=payload, timeout=10)
        if not resp.ok or not resp.json().get("success", False):
            log.error("addClient failed on %s: %s", server.name, resp.text[:200])
            return None
    except requests.RequestException as e:
        log.error("addClient request error on %s: %s", server.name, e)
        return None

    return client_uuid, server.name


def build_vless_link(client_uuid: str, user_id: int, server_name: str) -> str:
    """Собирает vless:// ссылку для конкретного сервера.

    Параметры pbk/sid/sni/spx/fp взяты из реального inbound-а на сервере (скопированы
    один раз из панели 3x-ui). Если пересоздашь inbound с нуля — Reality сгенерирует новые
    pbk/sid, и эти значения здесь придется обновить на новые (возьми их тем же способом:
    Клиенты -> иконка QR/ссылки -> разверни строку Vless/TCP/REALITY -> скопируй ссылку)."""
    server = next(s for s in config.servers if s.name == server_name)
    email = f"user_{user_id}"
    return (
        f"vless://{client_uuid}@{server.public_host}:{server.public_port}"
        f"?encryption=none&fp=chrome"
        f"&pbk=3DMQMj9YBb9g8Lvk_9qppzIDODw7Jdskvqit-zHrkVc"
        f"&security=reality&sid=34&sni=www.aws.amazon.com"
        f"&spx=%2F4656pTuO5U3meAQ&type=tcp#VPNMonkey-{email}"
    )


def disable_client(server_name: str, client_uuid: str) -> bool:
    """Заглушка для отключения ключа при истечении подписки.
    Реализуется через POST /panel/api/inbounds/{inboundId}/updateClient/{uuid}
    с enable=false — сделай по аналогии с create_vpn_client, когда дойдешь до
    автоматической деактивации просрочек (см. main.py -> TODO периодическая проверка)."""
    return True
