@echo off
setlocal
cd /d "%~dp0"
set "LOGFILE=%~dp0publisher_startup.log"
echo WordPress Folder Publisher startup log > "%LOGFILE%"
echo Date: %date% %time% >> "%LOGFILE%"
echo Folder: %CD% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

REM Try common Python launch options first.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -u wordpress_folder_publisher_gui.py >> "%LOGFILE%" 2>&1
  if not errorlevel 1 goto :success
)

where python >nul 2>nul
if %errorlevel%==0 (
  python -u wordpress_folder_publisher_gui.py >> "%LOGFILE%" 2>&1
  if not errorlevel 1 goto :success
)

REM Try the user's known Python 3.13 installation directly.
set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if exist "%PYTHON_EXE%" (
  echo Trying "%PYTHON_EXE%" >> "%LOGFILE%"
  "%PYTHON_EXE%" -u wordpress_folder_publisher_gui.py >> "%LOGFILE%" 2>&1
  if not errorlevel 1 goto :success
)

REM Try common alternate install location.
set "PYTHON_EXE=C:\Users\gaurav\AppData\Local\Programs\Python\Python313\python.exe"
if exist "%PYTHON_EXE%" (
  echo Trying "%PYTHON_EXE%" >> "%LOGFILE%"
  "%PYTHON_EXE%" -u wordpress_folder_publisher_gui.py >> "%LOGFILE%" 2>&1
  if not errorlevel 1 goto :success
)

echo.
echo The publisher could not start. Details:
echo ---------------------------------------------------------------
type "%LOGFILE%"
echo ---------------------------------------------------------------
echo.
echo Diagnostic log saved to "%LOGFILE%"
pause
exit /b 1

:success
echo.
echo Publisher closed. Startup log saved to "%LOGFILE%"
