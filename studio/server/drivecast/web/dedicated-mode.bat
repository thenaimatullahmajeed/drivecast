@echo off
title DriveCast - Dedicated phone mode
echo.
echo  DriveCast - Dedicated phone mode (one time)
echo  ------------------------------------------------------------
echo  Before you run this:
echo   1. The phone was factory reset and NO Google account was added.
echo   2. DriveCast Server is installed and opened once.
echo   3. Settings ^> About phone ^> tap "Build number" 7 times,
echo      then Developer options ^> USB debugging = ON.
echo   4. Phone connected to this PC with a USB cable; allow the prompt.
echo.
where adb >nul 2>nul
if errorlevel 1 (
  if exist "%~dp0platform-tools\adb.exe" (
    set "ADB=%~dp0platform-tools\adb.exe"
  ) else (
    echo  adb was not found. Download "SDK Platform-Tools for Windows" from
    echo  https://developer.android.com/tools/releases/platform-tools
    echo  and unzip it next to this file ^(folder name: platform-tools^).
    pause
    exit /b 1
  )
) else (
  set "ADB=adb"
)
"%ADB%" devices
echo.
echo  Making DriveCast the owner of this phone...
"%ADB%" shell dpm set-device-owner com.drivecast.server/.AdminReceiver
if errorlevel 1 (
  echo.
  echo  It did not work. The usual reason: an account is still on the phone
  echo  ^(Google, Samsung, Xiaomi...^). Remove all accounts or factory reset, then try again.
  pause
  exit /b 1
)
echo.
echo  Done. Open DriveCast on the phone and tap "Apply dedicated mode".
echo  You can switch it off later from the same screen.
pause
