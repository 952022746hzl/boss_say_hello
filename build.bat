@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "UV=uv"
where uv >nul 2>nul
if not errorlevel 1 goto sync
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
    goto sync
)
echo Install uv first: python -m pip install uv
goto failed

:sync
set "UV_CACHE_DIR=%CD%\.cache\uv"
set "PYTHONIOENCODING=utf-8"
echo Installing locked application and build dependencies...
"%UV%" sync --locked --group build
if errorlevel 1 goto failed
echo Building Windows executable...
".venv\Scripts\python.exe" scripts\build_exe.py
if errorlevel 1 goto failed
popd
if /i not "%~1"=="--no-pause" pause
exit /b 0

:failed
echo Build failed. See the error above.
popd
if /i not "%~1"=="--no-pause" pause
exit /b 1
