@echo off
rem Build the musiccil GUI. Requires a C++ compiler with Win32 API (MinGW-w64).
rem
rem Why these flags (MinGW's commctrl.h needs them or you get "not declared"):
rem   -DUNICODE -D_UNICODE   wide-char Win32 API, required for CJK text
rem   -mwindows              GUI subsystem, no console window
rem All linked libraries ship with Windows; nothing extra to install.
setlocal
cd /d "%~dp0"

if not exist build mkdir build

set CXX=g++
where %CXX% >nul 2>nul
if errorlevel 1 (
    echo [ERROR] g++ not found. Install MinGW-w64 and add it to PATH.
    exit /b 1
)

echo Building musiccil-gui.exe ...
%CXX% -std=c++11 -O2 -mwindows -DUNICODE -D_UNICODE -o build\musiccil-gui.exe src\main.cpp src\api.cpp src\json.cpp src\playlist.cpp -lwininet -lcomctl32 -luser32 -lgdi32 -lshell32 -lole32

if errorlevel 1 (
    echo [ERROR] build failed.
    exit /b 1
)

echo Done: build\musiccil-gui.exe
endlocal
