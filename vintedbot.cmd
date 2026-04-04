@echo off
if "%1" == "stop" (
    echo 🛑 Отправляю команду остановки всем экземплярам бота...
    curl -s "https://api.telegram.org/bot%BOT_TOKEN%/sendMessage?chat_id=%CHAT_ID%&text=%F0%9F%9B%91%20STOP%20ALL" > nul
    echo ✅ Команда отправлена! Все боты остановятся через 30 секунд.
    exit /b 0
)

echo Использование:
echo   vintedbot stop  - Остановить ВСЕ экземпляры бота на всех компьютерах
