@echo off
rem mgtkit アプリマネージャーの起動 (必要ライブラリの確認込み)
chcp 65001 >nul
title mgtkit アプリマネージャー
cd /d "%~dp0.."

set "PY=python"
where py >nul 2>nul && set "PY=py"

%PY% -c "import flet" >nul 2>nul
if errorlevel 1 (
    echo 必要ライブラリ ^(flet^) をインストールしています...
    %PY% -m pip install -r manager\requirements.txt
    if errorlevel 1 (
        echo インストールに失敗しました。ネットワーク接続を確認してください。
        pause
        exit /b 1
    )
)

%PY% -m manager.main
if errorlevel 1 pause
