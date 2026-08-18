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

rem 実際に動く Python を探す。where で在るかを見るだけだと、Windows 標準の
rem ストア誘導スタブ python.exe を「導入済み」と誤判定してしまい、
rem この先の pip がネットワークと無関係な理由で失敗する
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python が見つかりません。
    echo setup.bat をもう一度実行して Python を導入してから、
    echo あらためて起動してください。
    pause
    exit /b 1
)

%PY% -c "import flet" >nul 2>nul
if errorlevel 1 (
    echo 必要ライブラリ ^(flet^) をインストールしています...
    %PY% -m pip install -r manager\requirements.txt
    if errorlevel 1 (
        echo インストールに失敗しました。インターネットに接続できているか、
        echo 社内ネットワークが pypi.org を遮っていないか確認してください。
        pause
        exit /b 1
    )
)

%PY% -m manager.main
if errorlevel 1 pause
