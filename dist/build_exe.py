import PyInstaller.__main__
import os

PyInstaller.__main__.run([
    'main.py',
    '--onefile',
    '--windowed',
    '--name=vintedbot',
    '--icon=NONE',
    '--add-data', f'config.py{os.pathsep}.',
    '--hidden-import', 'aiogram',
    '--hidden-import', 'aiohttp',
    '--hidden-import', 'aiohttp_socks',
    '--hidden-import', 'bs4',
    '--hidden-import', 'dotenv',
])
