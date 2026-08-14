@echo off
chcp 65001 >nul
rem mgtkit アプリマネージャーの起動 (必要ライブラリの確認込み)
title mgtkit アプリマネージャー
cd /d "%~dp0.."

rem 起動のたびに最新のマネージャーへ更新する
rem (オフライン等で失敗しても、そのまま手元の版で起動する)
echo 最新版を確認しています...
git pull --ff-only >nul 2>nul
if errorlevel 1 echo ※ 最新化できませんでした。手元の版のまま起動します。

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
