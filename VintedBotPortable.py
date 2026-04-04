# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import tempfile
import requests
import zipfile
import io
import json
import base64
import winreg

GITHUB_REPO = "tellaboutme/qweqweqwe"
GITHUB_BRANCH = "master"
PYTHON_VERSION = "3.11.9"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
INSTALL_DIR = os.path.join(os.environ['APPDATA'], 'VintedBot')
CONFIG_FILE = os.path.join(INSTALL_DIR, 'config.dat')

def generate_key():
    """Генерируем уникальный ключ для этого компьютера"""
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
        return hwid.encode()
    except:
        return b'vinted_bot_default_key_123456'

def xor_encrypt(data: bytes, key: bytes) -> bytes:
    return bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])

def encrypt_data(data):
    key = generate_key()
    json_data = json.dumps(data).encode()
    encrypted = xor_encrypt(json_data, key)
    return base64.b64encode(encrypted)

def decrypt_data(encrypted_data):
    try:
        key = generate_key()
        decoded = base64.b64decode(encrypted_data)
        decrypted = xor_encrypt(decoded, key)
        return json.loads(decrypted.decode())
    except:
        return None

def save_config(bot_token, chat_id):
    os.makedirs(INSTALL_DIR, exist_ok=True)
    data = {'BOT_TOKEN': bot_token, 'CHAT_ID': chat_id}
    encrypted = encrypt_data(data)
    with open(CONFIG_FILE, 'wb') as f:
        f.write(encrypted)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, 'rb') as f:
        return decrypt_data(f.read())

def is_python_installed():
    try:
        subprocess.run(['python', '--version'], capture_output=True, check=True)
        return True
    except:
        return False

def install_python():
    print("🐍 Python не найден. Скачиваю портативную версию...")
    
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        python_dir = os.path.join(INSTALL_DIR, 'python')
        
        if os.path.exists(python_dir):
            return os.path.join(python_dir, 'python.exe')
            
        r = requests.get(PYTHON_URL, timeout=120)
        r.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(python_dir)
        
        # Install pip
        get_pip = requests.get('https://bootstrap.pypa.io/get-pip.py', timeout=60)
        get_pip_path = os.path.join(python_dir, 'get-pip.py')
        with open(get_pip_path, 'wb') as f:
            f.write(get_pip.content)
            
        subprocess.run([os.path.join(python_dir, 'python.exe'), get_pip_path], capture_output=True)
        
        print("✅ Python установлен успешно!")
        return os.path.join(python_dir, 'python.exe')
        
    except Exception as e:
        print(f"❌ Ошибка установки Python: {str(e)[:50]}")
        return None

def install_dependencies(python_path):
    print("📦 Устанавливаю зависимости...")
    subprocess.run([
        python_path, '-m', 'pip', 'install',
        'aiogram==3.15.0',
        'aiohttp==3.10.10',
        'aiohttp-socks==0.11.0',
        'beautifulsoup4==4.12.3',
        'requests==2.32.3',
        'python-dotenv==1.0.1',
        'cryptography==42.0.0'
    ], capture_output=True)

def update_bot():
    print("🔄 Обновляю бота до последней версии...")
    
    try:
        url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        
        bot_dir = os.path.join(INSTALL_DIR, 'bot')
        os.makedirs(bot_dir, exist_ok=True)
        
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            root_folder = z.namelist()[0].split('/')[0]
            for file in z.namelist():
                if not file.endswith('/') and file.endswith('.py'):
                    filename = file[len(root_folder)+1:]
                    if filename:
                        z.extract(file, tempfile.gettempdir())
                        os.rename(
                            os.path.join(tempfile.gettempdir(), file),
                            os.path.join(bot_dir, filename)
                        )
        
        print("✅ Бот обновлен!")
        return True
    except Exception as e:
        print(f"⚠️ Ошибка обновления: {str(e)[:50]}")
        return False

def add_to_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "VintedBot", 0, winreg.REG_SZ, f'"{sys.executable}"')
        winreg.CloseKey(key)
        return True
    except:
        return False

def main():
    # Check if running as exe
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    
    print("🚀 Vinted Bot Portable Launcher")
    print("="*40)
    
    # Check config
    config = load_config()
    if not config:
        print("\n⚠️ Первая настройка:")
        bot_token = input("Введите BOT_TOKEN: ").strip()
        chat_id = input("Введите CHAT_ID: ").strip()
        save_config(bot_token, chat_id)
        print("✅ Конфиг сохранен зашифрованным!")
    
    # Install Python if needed
    python_path = None
    if is_python_installed():
        python_path = 'python'
    else:
        python_path = install_python()
    
    if not python_path:
        input("❌ Не удалось установить Python. Нажмите Enter чтобы выйти.")
        sys.exit(1)
    
    # Install dependencies
    install_dependencies(python_path)
    
    # Update bot
    update_bot()
    
    # Add to startup
    add_to_startup()
    
    # Set environment variables
    config = load_config()
    os.environ['BOT_TOKEN'] = config['BOT_TOKEN']
    os.environ['CHAT_ID'] = config['CHAT_ID']
    
    print("\n✅ Все готово! Запускаю бота...")
    
    # Start bot completely hidden
    bot_dir = os.path.join(INSTALL_DIR, 'bot')
    main_py = os.path.join(bot_dir, 'main.py')
    
    if os.path.exists(main_py):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        
        subprocess.Popen(
            [python_path, main_py],
            cwd=bot_dir,
            env=os.environ.copy(),
            startupinfo=startupinfo,
            creationflags=0x08000000
        )
    
    print("✅ Бот запущен в фоновом режиме!")
    print("✅ Добавлен в автозапуск Windows")
    
    # Auto close after 3 seconds
    import time
    time.sleep(3)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        input("\nНажмите Enter чтобы выйти.")
