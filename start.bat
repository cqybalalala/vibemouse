@echo off
chcp 65001 >nul
set VIBEMOUSE_MODEL=C:\Users\User\.cache\modelscope\hub\models\iic\SenseVoiceSmall-onnx
set VIBEMOUSE_AUTO_PASTE=true
set VIBEMOUSE_LOG_LEVEL=INFO
set VIBEMOUSE_LANGUAGE=zh
set PYTHONIOENCODING=utf-8
rem Hotkey: Ctrl+Shift+F9  (VK codes: 162=LCtrl, 160=LShift, 120=F9)
set VIBEMOUSE_RECORD_HOTKEY_CODE_1=162
set VIBEMOUSE_RECORD_HOTKEY_CODE_2=160
set VIBEMOUSE_RECORD_HOTKEY_CODE_3=120
cd /d "%~dp0"
.venv\Scripts\python -c "from vibemouse.main import main; main(['run'])"
pause
