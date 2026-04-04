import os
import sys
import subprocess
import tempfile
import requests
import zipfile
import io

GITHUB_REPO = "tellaboutme/qweqweqwe"
GITHUB_BRANCH = "master"

def get_latest_version():
    """Download latest version from GitHub"""
    print("🔄 Загружаю последнюю версию бота...")
    
    try:
        url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        
        # Extract to temp directory
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            # Get root folder name inside zip
            root_folder = z.namelist()[0].split('/')[0]
            
            # Extract all files except launcher itself
            for file in z.namelist():
                if not file.endswith('/') and not file.endswith('VintedBotLauncher.py') and not file.endswith('vintedbotlauncher.py'):
                    filename = file[len(root_folder)+1:]
                    if filename:
                        try:
                            z.extract(file, tempfile.gettempdir())
                            os.rename(
                                os.path.join(tempfile.gettempdir(), file),
                                os.path.join(os.path.dirname(sys.argv[0]), filename)
                            )
                        except:
                            pass
        
        print("✅ Обновление завершено!")
        return True
    except Exception as e:
        print(f"⚠️ Ошибка обновления: {str(e)[:50]}")
        return False

def main():
    # Update to latest version
    get_latest_version()
    
    # Start the bot
    print("🚀 Запускаю бота...")
    bot_path = os.path.join(os.path.dirname(sys.argv[0]), 'main.py')
    subprocess.Popen([sys.executable, bot_path], creationflags=0x08000000)  # CREATE_NO_WINDOW

if __name__ == "__main__":
    main()
