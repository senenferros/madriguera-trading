@echo off
rem La Madriguera Trading: stops the panel (python.exe or pythonw.exe listening on port 5100).
setlocal
set "PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5100" ^| findstr "LISTENING"') do set "PID=%%p"
if not defined PID (
  echo El panel no esta encendido.
  goto fin
)
set "NOMBRE="
for /f "tokens=1 delims=," %%n in ('tasklist /fi "PID eq %PID%" /fo csv /nh') do set "NOMBRE=%%~n"
if /i not "%NOMBRE%"=="python.exe" if /i not "%NOMBRE%"=="pythonw.exe" (
  echo En el puerto 5100 hay otro programa ^(%NOMBRE%, PID %PID%^). No lo toco.
  goto fin
)
taskkill /pid %PID% /f >nul
if errorlevel 1 (
  echo No he podido apagar el panel ^(PID %PID%^).
) else (
  echo Panel apagado ^(%NOMBRE%, PID %PID%^).
)
:fin
timeout /t 3 >nul
endlocal
