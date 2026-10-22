@echo off
chcp 65001 >nul
setlocal
rem 이 파일이 있는 폴더를 기준으로 실행합니다 - 모든 경로는 상대경로
cd /d "%~dp0"
title MELLOY 캐릭터 검수기 - exe 만들기

echo ============================================
echo    MELLOY 캐릭터 검수기 exe 만들기
echo    처음에는 3~5분 정도 걸립니다.
echo ============================================
echo.

rem ---- 1. 파이썬 찾기 ----
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :check_pyside
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :check_pyside
echo [오류] 파이썬이 설치되어 있지 않습니다. 먼저 실행하기.bat 안내를 따라 설치해 주세요.
pause
exit /b 1

:check_pyside
%PY% -c "import PySide6" >nul 2>&1
if not errorlevel 1 goto :check_pyinstaller
echo [오류] PySide6 가 없습니다. 먼저 실행하기.bat 을 한 번 실행해 주세요.
pause
exit /b 1

rem ---- 2. exe 를 만드는 도구 PyInstaller 확인 및 설치 ----
:check_pyinstaller
%PY% -c "import PyInstaller" >nul 2>&1
if not errorlevel 1 goto :check_app
echo [설치] exe 를 만드는 도구 PyInstaller 를 설치합니다...
%PY% -m pip install pyinstaller
%PY% -c "import PyInstaller" >nul 2>&1
if not errorlevel 1 goto :check_app
%PY% -m pip install --user pyinstaller
%PY% -c "import PyInstaller" >nul 2>&1
if not errorlevel 1 goto :check_app
echo [오류] PyInstaller 설치에 실패했습니다. 인터넷 연결을 확인해 주세요.
pause
exit /b 1

:check_app
%PY% -c "import app" >nul 2>&1
if not errorlevel 1 goto :build
echo [오류] app.py 를 여는 중 문제가 생겼습니다. 아래 내용을 복사해서 전달해 주세요.
%PY% -c "import app"
pause
exit /b 1

rem ---- 3. exe 만들기 - 폴더 형태: exe + _internal 폴더 / 작업 파일은 build 폴더에 모읍니다 ----
:build
echo [진행] exe 를 만드는 중입니다. 창을 닫지 마세요...
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --name MELLOY_Checker --distpath build\dist --workpath build\work --specpath build app.py
if errorlevel 1 goto :build_fail
if not exist "build\dist\MELLOY_Checker\MELLOY_Checker.exe" goto :build_fail
set "DIST=build\dist\MELLOY_Checker"

rem ---- 4. 오픈소스 고지 준비 ----
set "LIC=build\라이선스"
if exist "%LIC%" rmdir /s /q "%LIC%"
xcopy "라이선스" "%LIC%\" /E /I /Y >nul
%PY% -c "import PySide6; print('PySide6', PySide6.__version__)" > "%LIC%\PySide6_버전.txt"
set "PYLIC="
for /f "usebackq delims=" %%i in (`%PY% -c "import sys;import os;print(sys.base_prefix+os.sep+'LICENSE.txt')"`) do set "PYLIC=%%i"
set "PYLIC_OK="
if defined PYLIC if exist "%PYLIC%" set "PYLIC_OK=1"
if defined PYLIC_OK copy /Y "%PYLIC%" "%LIC%\Python_LICENSE.txt" >nul
if not defined PYLIC_OK echo Python 라이선스 전문: https://docs.python.org/3/license.html> "%LIC%\Python_LICENSE.txt"

rem ---- 5. 내가 쓸 폴더: 배포\MELLOY 캐릭터 검수기 ----
rem 프로그램 파일은 매번 새로 바꾸고, rules 와 예시는 이 폴더 쪽이 더 새로울 때만 덮어씁니다 - 배포 폴더에서 고친 내용 보호
set "OUT=배포\MELLOY 캐릭터 검수기"
if not exist "%OUT%" mkdir "%OUT%"
if exist "%OUT%\_internal" rmdir /s /q "%OUT%\_internal"
if exist "%OUT%\_internal" goto :copy_fail
xcopy "%DIST%\_internal" "%OUT%\_internal\" /E /I /Y /Q >nul
if errorlevel 1 goto :copy_fail
copy /Y "%DIST%\MELLOY_Checker.exe" "%OUT%\MELLOY 캐릭터 검수기.exe" >nul
if errorlevel 1 goto :copy_fail
xcopy "rules" "%OUT%\rules\" /E /I /Y /D >nul
xcopy "예시" "%OUT%\예시\" /E /I /Y /D >nul
copy /Y "사용안내.txt" "%OUT%\사용안내.txt" >nul
xcopy "%LIC%" "%OUT%\라이선스\" /E /I /Y >nul

rem ---- 6. 지인에게 보낼 폴더: 배포\공유용\MELLOY 캐릭터 검수기 ----
rem 매번 깨끗하게 새로 만듭니다 - 내 생성결과, 검수결과 같은 개인 파일이 섞이지 않도록
set "SHARE=배포\공유용\MELLOY 캐릭터 검수기"
if exist "배포\공유용" rmdir /s /q "배포\공유용"
if exist "배포\공유용" goto :copy_fail
mkdir "%SHARE%"
xcopy "%DIST%\_internal" "%SHARE%\_internal\" /E /I /Y /Q >nul
if errorlevel 1 goto :copy_fail
copy /Y "%DIST%\MELLOY_Checker.exe" "%SHARE%\MELLOY 캐릭터 검수기.exe" >nul
if errorlevel 1 goto :copy_fail
xcopy "rules" "%SHARE%\rules\" /E /I /Y >nul
xcopy "예시" "%SHARE%\예시\" /E /I /Y >nul
copy /Y "사용안내.txt" "%SHARE%\사용안내.txt" >nul
xcopy "%LIC%" "%SHARE%\라이선스\" /E /I /Y >nul

echo.
echo ============================================
echo  완료되었습니다.
echo.
echo  [내가 쓸 프로그램]  배포\MELLOY 캐릭터 검수기
echo    폴더째 원하는 곳으로 옮겨서 쓰시면 됩니다.
echo.
echo  [지인에게 보낼 것]  배포\공유용\MELLOY 캐릭터 검수기
echo    이 폴더를 오른쪽 클릭 - 보내기 - 압축(ZIP) 폴더 로 압축해서 보내세요.
echo    Windows 11 은 오른쪽 클릭 - ZIP 파일로 압축 입니다.
echo    내 생성결과, 검수결과 파일은 들어가지 않습니다. rules 폴더는 들어갑니다.
echo ============================================
echo.
echo 처음 실행할 때 "Windows의 PC 보호" 창이 뜨면
echo [추가 정보] - [실행] 을 누르세요. 직접 만든 프로그램이라 서명이 없어서 뜨는 안내입니다.
echo.
start "" explorer "배포\공유용"
pause
exit /b 0

:build_fail
if not exist "build\dist\MELLOY_Checker.exe" goto :build_fail

rem ---- 4. 배포 폴더 정리: exe + rules + 예시 ----
set "OUT=배포\MELLOY 캐릭터 검수기"
if not exist "%OUT%" mkdir "%OUT%"
copy /Y "build\dist\MELLOY_Checker.exe" "%OUT%\MELLOY 캐릭터 검수기.exe" >nul
if errorlevel 1 goto :copy_fail
rem rules 와 예시는 이 폴더 쪽이 더 새로울 때만 덮어씁니다 - 배포 폴더에서 고친 내용 보호
xcopy "rules" "%OUT%\rules\" /E /I /Y /D >nul
xcopy "예시" "%OUT%\예시\" /E /I /Y /D >nul

echo.
echo ============================================
echo  완료되었습니다.
echo  배포\MELLOY 캐릭터 검수기 폴더를 원하는 곳으로 옮겨서 쓰시면 됩니다.
echo  exe 와 rules 폴더는 항상 같은 폴더 안에 두어야 합니다.
echo ============================================
echo.
echo 처음 실행할 때 "Windows의 PC 보호" 창이 뜨면
echo [추가 정보] - [실행] 을 누르세요. 직접 만든 프로그램이라 서명이 없어서 뜨는 안내입니다.
echo.
start "" explorer "%OUT%"
pause
exit /b 0

:build_fail
echo.
echo [오류] exe 를 만들지 못했습니다. 위의 내용을 복사해서 전달해 주세요.
pause
exit /b 1

:copy_fail
echo.
echo [오류] exe 를 배포 폴더로 복사하지 못했습니다.
echo        배포 폴더에서 실행 중인 검수기 프로그램이나, 배포 폴더 안을 열어 둔 탐색기 창이 있으면
echo        모두 닫고 다시 시도해 주세요.
pause
exit /b 1
