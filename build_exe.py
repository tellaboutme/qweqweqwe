import PyInstaller.__main__
import os

PyInstaller.__main__.run([
    'main.py',
    '--onefile',
    '--windowed',
    '--name=VintedBot',
    '--icon=NONE',
    '--add-data', f'config.py{os.pathsep}.',
    '--add-data', f'vinted_monitor.py{os.pathsep}.',
    '--add-data', f'proxy_manager.py{os.pathsep}.',
    '--add-data', f'user_storage.py{os.pathsep}.',
    '--hidden-import', 'aiogram',
    '--hidden-import', 'aiohttp',
    '--hidden-import', 'aiohttp_socks',
    '--hidden-import', 'bs4',
    '--hidden-import', 'dotenv',
])
