#!/bin/bash

# Автоматический скрипт установки для Oracle Cloud Ubuntu 22.04
# Запуск: sudo bash install.sh

echo "🔧 Обновляем систему..."
apt update && apt upgrade -y

echo "🐍 Устанавливаем Python..."
apt install python3 python3-pip python3-venv git -y

echo "📂 Создаем папку для бота..."
mkdir -p /home/ubuntu/vinted-bot
cd /home/ubuntu/vinted-bot

echo "📦 Устанавливаем зависимости..."
pip3 install -r requirements.txt

echo "⚙️ Устанавливаем службу systemd..."
cp vinted-bot.service /etc/systemd/system/

echo "🔄 Перезагружаем systemd..."
systemctl daemon-reload

echo "✅ Включаем автозапуск при старте системы..."
systemctl enable vinted-bot

echo ""
echo "✅ Установка завершена!"
echo ""
echo "Далее сделай это:"
echo "1. Отредактируй файл .env и добавь свои BOT_TOKEN и CHAT_ID"
echo "2. Запусти бота: sudo systemctl start vinted-bot"
echo "3. Проверь статус: sudo systemctl status vinted-bot"
echo "4. Посмотреть логи: journalctl -u vinted-bot -f"
echo ""
