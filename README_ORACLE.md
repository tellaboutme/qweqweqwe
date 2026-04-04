# Развертывание на Oracle Cloud Free Tier

✅ Бот будет работать 24/7 БЕСПЛАТНО НАВСЕГДА

---

## 🚀 Шаг 1: Создаем инстанс в Oracle Cloud

1.  Заходим в Oracle Cloud Console
2.  Создаем Compute Instance:
    - Image: **Ubuntu 22.04 Minimal**
    - Shape: **VM.Standard.A1.Flex** (4 ядра / 24 ГБ ОЗУ - БЕСПЛАТНО)
    - Скачиваем приватный ключ
3.  Открываем порт 22 в Security Lists для SSH доступа

---

## 🚀 Шаг 2: Подключаемся по SSH

```bash
ssh -i /путь/к/ключу.pem ubuntu@<IP-АДРЕС-ОРАКЛА>
```

---

## 🚀 Шаг 3: Автоматическая установка

```bash
# Клонируем репозиторий
git clone https://github.com/tellaboutme/qweqweqwe.git vinted-bot
cd vinted-bot

# Запускаем автоматический скрипт установки
sudo bash install.sh
```

---

## 🚀 Шаг 4: Настраиваем переменные

Создаем файл `.env` в папке бота:
```ini
BOT_TOKEN=твой_токен_от_бота_от_BotFather
CHAT_ID=твой_айди_чата
```

---

## 🚀 Шаг 5: Запускаем бота

```bash
# Запускаем службу
sudo systemctl start vinted-bot

# Проверяем что все работает
sudo systemctl status vinted-bot

# Смотрим логи в реальном времени
journalctl -u vinted-bot -f
```

---

## 📋 Полезные команды

| Команда | Что делает |
|---|---|
| `sudo systemctl start vinted-bot` | Запустить бота |
| `sudo systemctl stop vinted-bot` | Остановить бота |
| `sudo systemctl restart vinted-bot` | Перезапустить бота |
| `sudo systemctl status vinted-bot` | Статус бота |
| `journalctl -u vinted-bot -f` | Смотреть логи в реальном времени |
| `sudo systemctl disable vinted-bot` | Отключить автозапуск |

---

✅ Готово! Бот будет работать 24/7, даже когда твой комп выключен. Он автоматически перезапустится после любых перезагрузок сервера.
