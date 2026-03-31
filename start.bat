@echo off
title Gramophone

:: Check Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Node.js not found.
    echo  Please install from https://nodejs.org
    echo.
    pause
    exit /b 1
)

:: Check Python
where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  [ERROR] Python not found.
        echo  Please install from https://python.org
        echo  Make sure to check "Add Python to PATH" during install.
        echo.
        pause
        exit /b 1
    )
)

:: Install npm deps if needed
if not exist "node_modules\" (
    echo  Installing Electron ^(one-time, ~200 MB^)...
    npm install
    if errorlevel 1 (
        echo  [ERROR] npm install failed.
        pause
        exit /b 1
    )
)

:: Launch
echo  Starting Gramophone...
npm start
