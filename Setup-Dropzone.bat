@echo off
copy /Y "%~dp0setup-windows.ps1" "%TEMP%\dropzone-setup.ps1" >nul
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%TEMP%\dropzone-setup.ps1""'"
