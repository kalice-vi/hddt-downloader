@echo off
rem HDDT Downloader - tu cai dat neu thieu moi truong
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv\Scripts\python.exe"

rem ---------- venv hop le thi mo app ----------
if not exist "%VENV%" goto :install
"%VENV%" -c "import playwright, segno; import playwright.sync_api" >nul 2>&1
if errorlevel 1 goto :install
"%VENV%" -m playwright --version >nul 2>&1
if errorlevel 1 goto :install
goto :run

:install
echo ===== Dang cai dat moi truong lan dau. Can mang internet. =====

rem xoa venv hong neu co
"%VENV%" -c "pass" >nul 2>&1
if not errorlevel 1 goto :install_libs
if exist "%VENV%" rmdir /s /q ".venv"

rem tim python, bao gom truong hop khong co
set "PYCMD="
where py >nul 2>&1
if errorlevel 1 goto :try_py2
py -3 -c "pass" >nul 2>&1
if errorlevel 1 goto :try_py2
set "PYCMD=py -3"
goto :mkvenv
:try_py2
python -c "pass" >nul 2>&1
if errorlevel 1 goto :try_py3
set "PYCMD=python"
goto :mkvenv
:try_py3
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not "%PYCMD%"=="" goto :mkvenv
set "PYCMD="
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not "%PYCMD%"=="" goto :mkvenv
set "PYCMD="
if exist "C:\Python312\python.exe" set "PYCMD=C:\Python312\python.exe"
if not "%PYCMD%"=="" goto :mkvenv
set "PYCMD="
goto :setup_uv
:mkvenv
%PYCMD% -m venv .venv
if errorlevel 1 goto :err
goto :install_libs

rem ---------- khong co python van dung duoc: uv tu tai python ----------
:setup_uv
echo ===== Khong tim thay Python he thong. Dung uv (tu tai). =====
set "UVBIN=%~dp0.uv\uv.exe"
if not exist "%UVBIN%" goto :get_uv
"%UVBIN%" --version >nul 2>&1
if errorlevel 1 goto :get_uv
goto :uv_go
:get_uv
echo Dang tai uv ...
mkdir "%~dp0.uv" 2>nul
set "UVZIP=%TEMP%\uv_dl.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$url='https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip';[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;Invoke-WebRequest -Uri $url -OutFile '%UVZIP%';Expand-Archive -Path '%UVZIP%' -DestinationPath '%~dp0.uv_tmp' -Force;$f=Get-ChildItem -Path '%~dp0.uv_tmp' -Recurse -Filter uv.exe | Select-Object -First 1;if($f){Move-Item $f.FullName '%~dp0.uv\uv.exe' -Force}else{exit 1}"
if errorlevel 1 goto :err
rmdir /s /q "%~dp0.uv_tmp" 2>nul
:uv_go
set "UV_DEFAULT_INDEX=https://pypi.org/simple"
"%UVBIN%" python install 3.11
if errorlevel 1 goto :err
"%UVBIN%" venv .venv --python 3.11 --seed
if errorlevel 1 goto :err

rem ---------- cai thu vien ----------
:install_libs
call "%~dp0.venv\Scripts\activate.bat"
set "UV_DEFAULT_INDEX=https://pypi.org/simple"
python -m pip install --upgrade pip >nul 2>&1
pip install playwright pillow segno
if errorlevel 1 goto :err
python -m playwright install chromium
if errorlevel 1 goto :err
echo ===== Cai dat xong. Mo app... =====
goto :run

rem ---------- mo app ----------
:run
"%VENV%" "%~dp0hddt_app.py"
if errorlevel 1 (
    echo.
    echo Co loi khi chay app. Xoa .venv roi mo lai de cai lai.
    pause
)
exit /b 0

:err
echo.
echo ===== LOI cai dat. Kiem tra mang hoac thu lai. =====
pause
exit /b 1
