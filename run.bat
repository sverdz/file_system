@echo off
chcp 65001 > nul

REM Перевірка чи скрипт запущено з правильної директорії
if not exist "app\main.py" (
    echo [ПОМИЛКА] Запустіть цей скрипт з кореневої директорії проекту!
    echo.
    echo Поточна директорія: %CD%
    echo Очікувана структура:
    echo   - app\main.py
    echo   - requirements.txt
    echo   - run.bat
    echo.
    echo Використайте команду: cd /d "C:\Users\dsver\OneDrive\Documents\GitHub\file_system"
    echo Або запустіть скрипт двічі клікнувши по ньому в провіднику Windows
    pause
    exit /b 1
)

REM Створення віртуального середовища
if not exist .venv (
    echo Створення віртуального середовища...
    python -m venv .venv
    if errorlevel 1 (
        echo [ПОМИЛКА] Не вдалося створити віртуальне середовище
        echo Переконайтеся що Python встановлено та доступний в PATH
        pause
        exit /b 1
    )
)

REM Активація віртуального середовища
set "PYTHONUTF8=1"
call .venv\Scripts\activate.bat

REM Перевірка чи встановлені залежності
python -c "import colorama" 2>nul
if errorlevel 1 (
    echo Встановлення залежностей...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ПОМИЛКА] Не вдалося встановити залежності
        pause
        exit /b 1
    )
)

REM Запуск програми
python -m app.main %*
