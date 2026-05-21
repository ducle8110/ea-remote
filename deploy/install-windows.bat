@echo off
REM =====================================================================
REM Install ea-remote như Windows services qua NSSM:
REM   1. EARemoteHub       — waitress chạy Flask app trên 127.0.0.1:5000
REM   2. EARemoteTunnel    — cloudflared expose ra HTTPS public URL
REM
REM Yêu cầu trước khi chạy:
REM   - Python 3.11+ trong PATH
REM   - NSSM trong PATH (copy nssm.exe vào C:\Windows\System32\)
REM   - cloudflared.exe trong PATH (Cloudflare MSI installer)
REM   - File .env ở root repo với các secret (SECRET_KEY, ADMIN_PASSWORD, ...)
REM
REM Chạy bằng QUYỀN ADMINISTRATOR.
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

set REPO_ROOT=%CD%
set HUB_SERVICE=EARemoteHub
set TUNNEL_SERVICE=EARemoteTunnel
set RUN_BAT=%REPO_ROOT%\deploy\run-waitress.bat
set LOG_DIR=%REPO_ROOT%\logs
set HUB_LOG=%LOG_DIR%\ea-remote.log
set TUNNEL_LOG=%LOG_DIR%\cloudflared.log

REM ----- Sanity checks -----
where python >nul 2>&1 || (
    echo [ERROR] Python khong tim thay trong PATH. Cai Python 3.11+ truoc.
    exit /b 1
)
where nssm >nul 2>&1 || (
    echo [ERROR] NSSM khong tim thay. Tai tu https://nssm.cc/download va copy nssm.exe vao C:\Windows\System32\
    exit /b 1
)
where cloudflared >nul 2>&1 || (
    echo [WARN] cloudflared chua cai. EARemoteTunnel se khong start duoc.
    echo Tai cloudflared MSI tu https://github.com/cloudflare/cloudflared/releases
)
if not exist "%RUN_BAT%" (
    echo [ERROR] %RUN_BAT% khong ton tai.
    exit /b 1
)

if not exist "%REPO_ROOT%\.env" (
    echo [WARN] File .env chua co o %REPO_ROOT%. Tao truoc khi start service.
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ----- Install Python deps -----
echo [1/5] Cai Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r remote\requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install fail
    exit /b 1
)

REM ----- Remove old services if exist -----
echo [2/5] Stop + remove old services neu co...
nssm stop %HUB_SERVICE% >nul 2>&1
nssm remove %HUB_SERVICE% confirm >nul 2>&1
nssm stop %TUNNEL_SERVICE% >nul 2>&1
nssm remove %TUNNEL_SERVICE% confirm >nul 2>&1

REM ----- Register hub service -----
echo [3/5] Register %HUB_SERVICE% (waitress)...
nssm install %HUB_SERVICE% "%RUN_BAT%"
nssm set %HUB_SERVICE% AppDirectory "%REPO_ROOT%"
nssm set %HUB_SERVICE% AppStdout "%HUB_LOG%"
nssm set %HUB_SERVICE% AppStderr "%HUB_LOG%"
nssm set %HUB_SERVICE% AppRotateFiles 1
nssm set %HUB_SERVICE% AppRotateBytes 10485760
nssm set %HUB_SERVICE% Start SERVICE_AUTO_START
nssm set %HUB_SERVICE% DisplayName "EA Remote Hub (Flask + waitress)"
nssm set %HUB_SERVICE% Description "Copy-trade hub + admin dashboard. Listen 127.0.0.1:5000."

REM ----- Register cloudflared service (quick tunnel mode) -----
echo [4/5] Register %TUNNEL_SERVICE% (cloudflared quick tunnel)...
REM QUICK MODE: URL random *.trycloudflare.com — phai update WebRequest whitelist MT5 moi lan restart.
REM NAMED MODE: edit cloudflared-config.yml.example -> /etc/cloudflared/config.yml + uncomment block ben duoi.
nssm install %TUNNEL_SERVICE% cloudflared
nssm set %TUNNEL_SERVICE% AppParameters "tunnel --no-autoupdate --url http://127.0.0.1:5000"
REM NAMED MODE (uncomment + comment dong tren):
REM nssm set %TUNNEL_SERVICE% AppParameters "tunnel --no-autoupdate --config %REPO_ROOT%\deploy\cloudflared-config.yml run"
nssm set %TUNNEL_SERVICE% AppDirectory "%REPO_ROOT%"
nssm set %TUNNEL_SERVICE% AppStdout "%TUNNEL_LOG%"
nssm set %TUNNEL_SERVICE% AppStderr "%TUNNEL_LOG%"
nssm set %TUNNEL_SERVICE% AppRotateFiles 1
nssm set %TUNNEL_SERVICE% AppRotateBytes 10485760
nssm set %TUNNEL_SERVICE% Start SERVICE_AUTO_START
nssm set %TUNNEL_SERVICE% DisplayName "EA Remote Cloudflare Tunnel"
nssm set %TUNNEL_SERVICE% Description "HTTPS ingress qua Cloudflare → 127.0.0.1:5000"

REM ----- Start both services -----
echo [5/5] Start cac services...
nssm start %HUB_SERVICE%
nssm start %TUNNEL_SERVICE%

echo.
echo ===================================================================
echo INSTALLED OK
echo   - %HUB_SERVICE%    listen 127.0.0.1:5000 (log: %HUB_LOG%)
echo   - %TUNNEL_SERVICE% cloudflared quick tunnel (log: %TUNNEL_LOG%)
echo.
echo Steps tiep theo:
echo   1. Xem log de copy URL quick tunnel:
echo        type "%TUNNEL_LOG%"
echo        Tim dong "Your quick Tunnel has been created!" -- copy URL trycloudflare.com
echo   2. Mo browser https://^<sub^>.trycloudflare.com/login -- login admin
echo   3. Tao master User (neu chua) + enable copy-trade -- gen hmac_secret
echo   4. Tao Slave entities + lay token + secret cho moi slave
echo   5. Update RelayURL + MasterToken/SlaveToken + SharedSecret cua EA
echo.
echo Management:
echo   nssm restart %HUB_SERVICE%       (sau khi git pull)
echo   nssm status  %HUB_SERVICE%
echo   nssm stop    %HUB_SERVICE%
echo   tail-like:   powershell Get-Content -Wait "%HUB_LOG%"
echo ===================================================================
