@echo off
REM =====================================================================
REM Entry point cho NSSM Windows service `EARemoteHub`.
REM Khởi động waitress WSGI server serve Flask app.
REM
REM Chạy bằng NSSM (xem install-windows.bat) hoặc trực tiếp để test.
REM =====================================================================

setlocal
cd /d "%~dp0\.."

REM Đọc .env nếu có (NSSM env vars sẽ override)
if exist .env (
    for /f "tokens=1,* delims==" %%a in (.env) do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

REM Listen 127.0.0.1 only — cloudflared tunnel ra public, không expose port trực tiếp
python -m waitress --listen=127.0.0.1:5000 --threads=4 remote.run:app

endlocal
