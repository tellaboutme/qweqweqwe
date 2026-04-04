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
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        python_dir = os.path.join(INSTALL_DIR, 'python')
        
        if os.path.exists(python_dir):
            return os.path.join(python_dir, 'python.exe')
            
        r = requests.get(PYTHON_URL, timeout=120)
        r.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(python_dir)
        
        get_pip = requests.get('https://bootstrap.pypa.io/get-pip.py', timeout=60)
        get_pip_path = os.path.join(python_dir, 'get-pip.py')
        with open(get_pip_path, 'wb') as f:
            f.write(get_pip.content)
            
        subprocess.run([os.path.join(python_dir, 'python.exe'), get_pip_path], capture_output=True)
        
        return os.path.join(python_dir, 'python.exe')
        
    except Exception as e:
        return None

def install_dependencies(python_path):
    subprocess.run([
        python_path, '-m', 'pip', 'install',
        'aiogram==3.15.0',
        'aiohttp==3.10.10',
        'aiohttp-socks==0.11.0',
        'beautifulsoup4==4.12.3',
        'requests==2.32.3',
        'python-dotenv==1.0.1'
    ], capture_output=True)

def update_bot():
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
        
        return True
    except Exception as e:
        return False

def add_to_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "VintedBot", 0, winreg.REG_SZ, f'"{sys.executable}"')
        winreg.CloseKey(key)
        return True
    except:
        return False

def show_error(message):
    import tkinter as tk
    from tkinter import messagebox
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Vinted Bot", message)
        root.destroy()
    except:
        pass

def main():
    try:
        if getattr(sys, 'frozen', False):
            os.chdir(os.path.dirname(sys.executable))
        
        config = load_config()
        if not config:
            import tkinter as tk
            
            root = tk.Tk()
            root.title("Vinted Bot Setup")
            root.geometry("400x180")
            root.resizable(False, False)
            root.eval('tk::PlaceWindow . center')
            
            tk.Label(root, text="BOT_TOKEN:", font=("Arial", 10)).pack(pady=(20, 5))
            token_entry = tk.Entry(root, width=50, show="*")
            token_entry.pack(pady=5)
            token_entry.focus()
            
            tk.Label(root, text="CHAT_ID:", font=("Arial", 10)).pack(pady=(10, 5))
            chat_entry = tk.Entry(root, width=50)
            chat_entry.pack(pady=5)
            
            def on_submit():
                bot_token = token_entry.get().strip()
                chat_id = chat_entry.get().strip()
                if bot_token and chat_id:
                    save_config(bot_token, chat_id)
                    root.quit()
                    root.destroy()
            
            tk.Button(root, text="OK", command=on_submit, width=20).pack(pady=15)
            root.mainloop()
        
        python_path = None
        if is_python_installed():
            python_path = 'python'
        else:
            python_path = install_python()
        
        if not python_path:
            show_error("Не удалось установить Python")
            return
        
        install_dependencies(python_path)
        update_bot()
        add_to_startup()
        
        config = load_config()
        if config:
            os.environ['BOT_TOKEN'] = config['BOT_TOKEN']
            os.environ['CHAT_ID'] = config['CHAT_ID']
        
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
        
        import time
        time.sleep(1)
        
    except Exception as e:
        show_error(f"Ошибка: {str(e)}")

if __name__ == "__main__":
    main()
