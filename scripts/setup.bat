@echo off
rem ============================================================
rem  mgtkit セットアップ (メンバー配布用)
rem  このファイルだけを新しいメンバーに渡してダブルクリックしてもらう。
rem  1. winget で Git / GitHub CLI / Python を導入 (未導入のもののみ)
rem  2. GitHub へログイン (ブラウザが開きます)
rem  3. mgtkit を取得 (%USERPROFILE%\mgtkit)
rem  4. アプリマネージャーを起動
rem ============================================================
chcp 65001 >nul
setlocal
title mgtkit セットアップ
set "REPO=takimoto0223/mgtkit"
set "DEST=%USERPROFILE%\mgtkit"

echo === mgtkit セットアップを開始します ===
echo.

where winget >nul 2>nul
if errorlevel 1 (
    echo winget が見つかりません。Windows 10/11 の「アプリ インストーラー」を
    echo Microsoft Store から導入してから、もう一度実行してください。
    pause
    exit /b 1
)

where git >nul 2>nul
if errorlevel 1 (
    echo [1/4] Git をインストールしています...
    winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
) else (
    echo [1/4] Git: 導入済み
)

where gh >nul 2>nul
if errorlevel 1 (
    echo [2/4] GitHub CLI をインストールしています...
    winget install --id GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
) else (
    echo [2/4] GitHub CLI: 導入済み
)

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [3/4] Python をインストールしています...
        winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
    ) else (
        echo [3/4] Python: 導入済み
    )
) else (
    echo [3/4] Python: 導入済み
)

rem インストール直後は PATH が反映されていないため、新しい環境で続行する
set "PATH=%ProgramFiles%\Git\cmd;%ProgramFiles%\GitHub CLI;%LocalAppData%\Programs\Python\Python311;%PATH%"

echo.
echo [4/4] GitHub へログインします (ブラウザが開いたら指示に従ってください)
gh auth status >nul 2>nul
if errorlevel 1 (
    gh auth login --hostname github.com --git-protocol https --web
    if errorlevel 1 (
        echo ログインできませんでした。もう一度実行してください。
        pause
        exit /b 1
    )
) else (
    echo GitHub: ログイン済み
)
gh auth setup-git >nul 2>nul

echo.
if exist "%DEST%\.git" (
    echo mgtkit は取得済みです。最新にしています...
    git -C "%DEST%" pull
) else (
    echo mgtkit を取得しています...
    git clone "https://github.com/%REPO%.git" "%DEST%"
    if errorlevel 1 (
        echo 取得に失敗しました。リポジトリへのアクセス権を管理者に確認してください。
        pause
        exit /b 1
    )
)

echo.
echo === セットアップ完了。マネージャーを起動します ===
call "%DEST%\manager\マネージャー起動.bat"
endlocal
