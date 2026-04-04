Set WshShell = CreateObject("WScript.Shell")

' Добавляем бота в автозапуск при старте Windows
Set reg = WshShell.RegWrite("HKCU\Software\Microsoft\Windows\CurrentVersion\Run\VintedBot", Chr(34) & WScript.ScriptFullName & Chr(34), "REG_SZ")

' Запускаем бота полностью в фоне, без окон
botDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = botDir
WshShell.Run "pythonw.exe main.py", 0, False

MsgBox "✅ Бот добавлен в автозапуск и запущен в фоне!", vbInformation, "Vinted Bot"
