@echo off
chcp 65001 >nul
setlocal
rem 이 파일이 있는 폴더를 기준으로 실행합니다 - 모든 경로는 상대경로
cd /d "%~dp0"
title MELLOY 캐릭터 검수기 v0.1

echo ============================================
echo    MELLOY 캐릭터 검수기 v0.1 실행 준비 중...
echo ============================================
echo.

rem ---- 1. 파이썬 찾기 ----
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :check_version
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :check_version
goto :no_python

:check_version
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :old_python
echo [확인] 파이썬이 설치되어 있습니다.

rem ---- 2. PySide6 확인 및 자동 설치 ----
%PY% -c "import PySide6" >nul 2>&1
if not errorlevel 1 goto :pyside_ok
echo [설치] 화면 라이브러리 PySide6 를 설치합니다. 처음 한 번만, 몇 분 걸릴 수 있습니다...
%PY% -m pip install -r requirements.txt
%PY% -c "import PySide6" >nul 2>&1
if not errorlevel 1 goto :pyside_ok
echo [재시도] 사용자 계정 전용으로 다시 설치합니다...
%PY% -m pip install --user -r requirements.txt
%PY% -c "import PySide6" >nul 2>&1
if errorlevel 1 goto :pyside_fail

:pyside_ok
echo [확인] 화면 라이브러리 PySide6 가 준비되었습니다.

rem ---- 3. Claude Code 확인 - 없어도 기계 검사는 가능 ----
where claude >nul 2>&1
if not errorlevel 1 goto :claude_ok
rem PATH 에 아직 등록되지 않았을 때를 대비해 공식 설치 위치도 확인합니다
if exist "%USERPROFILE%\.local\bin\claude.exe" goto :claude_ok
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\claude.exe" goto :claude_ok
if exist "%APPDATA%\npm\claude.cmd" goto :claude_ok
goto :no_claude

:claude_ok
echo [확인] Claude Code 가 설치되어 있습니다. AI 의미 검수를 사용할 수 있습니다.
goto :run

:no_claude
echo [안내] Claude Code 가 설치되어 있지 않습니다.
echo        기계 검사는 사용할 수 있지만, AI 의미 검수는 사용할 수 없습니다.
echo        설치 방법은 프로그램에서 AI 의미 검수 버튼을 누르면 안내됩니다.

:run
echo.
rem 먼저 프로그램 파일에 문제가 없는지 확인합니다 - 문제가 있으면 이 창에 내용이 보입니다
%PY% -c "import app" >nul 2>&1
if errorlevel 1 goto :app_check_error

rem 검은 창 없이 실행하는 pythonw 를 찾습니다
set "PYW="
for /f "usebackq delims=" %%i in (`%PY% -c "import sys;import os;print(os.path.dirname(sys.executable)+os.sep+'pythonw.exe')"`) do set "PYW=%%i"
if not defined PYW goto :run_console
if not exist "%PYW%" goto :run_console
echo 프로그램을 실행합니다. 이 검은 창은 곧 닫힙니다.
start "" "%PYW%" app.py
exit /b 0

:run_console
echo 프로그램을 실행합니다. 이 검은 창은 프로그램을 닫으면 자동으로 닫힙니다.
%PY% app.py
if errorlevel 1 goto :app_error
exit /b 0

:app_check_error
echo.
echo [오류] 프로그램 파일을 여는 중 문제가 생겼습니다. 아래 내용을 복사해서 전달해 주세요.
echo.
%PY% -c "import app"
pause
exit /b 1

:app_error
echo.
echo [오류] 프로그램이 오류로 종료되었습니다. 위의 내용을 복사해서 전달해 주세요.
pause
exit /b 1

:no_python
echo [오류] 파이썬이 설치되어 있지 않습니다.
echo.
echo  1. https://www.python.org/downloads/ 에서 Python 을 내려받아 설치하세요.
echo  2. 설치 첫 화면 아래쪽의 "Add python.exe to PATH" 에 반드시 체크하세요.
echo  3. 설치가 끝나면 이 파일을 다시 더블클릭하세요.
echo.
pause
exit /b 1

:old_python
echo [오류] 파이썬 버전이 너무 낮습니다. 3.9 이상이 필요합니다.
echo        https://www.python.org/downloads/ 에서 최신 버전을 설치해 주세요.
echo.
pause
exit /b 1

:pyside_fail
echo.
echo [오류] PySide6 설치에 실패했습니다.
echo        인터넷 연결을 확인한 뒤 이 파일을 다시 실행해 주세요.
echo.
pause
exit /b 1
