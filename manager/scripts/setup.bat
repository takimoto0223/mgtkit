@echo off
chcp 65001 >nul
rem ============================================================
rem  mgtkit セットアップ (メンバー配布用)
rem  このファイルだけを新しいメンバーに渡してダブルクリックしてもらう。
rem  1. winget で Git / GitHub CLI / Python を導入 (未導入のもののみ)
rem  2. GitHub へログイン (ブラウザが開きます)
rem  3. mgtkit を取得 (%USERPROFILE%\mgtkit)
rem  4. アプリマネージャーを起動
rem
rem  導入の判定は「コマンドが在るか」(where) ではなく「実際に動くか」で
rem  行う。Windows 10/11 は既定で Microsoft Store への誘導スタブ
rem  python.exe を PATH に持っており、where では未導入を「導入済み」と
rem  誤判定してしまうため (新しいメンバーが最も踏みやすい失敗)。
rem  winget の結果も必ず確認する。素通りすると後段の git clone が
rem  「コマンドが無い」で落ち、原因と無関係な案内が出てしまう。
rem ============================================================
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

rem 前回の導入が PATH に未反映のことがあるので、判定の前に補っておく
call :add_tool_paths

rem ---------------- [1/4] Git ----------------
git --version >nul 2>nul
if not errorlevel 1 (
    echo [1/4] Git: 導入済み
    goto :git_done
)
echo [1/4] Git をインストールしています...
winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
set "RC=%errorlevel%"
call :add_tool_paths
git --version >nul 2>nul
if not errorlevel 1 (
    echo [1/4] Git: 導入しました
    goto :git_done
)
set "TOOL=Git"
set "TOOL_URL=https://git-scm.com/"
if not "%RC%"=="0" goto :install_failed
goto :need_restart
:git_done

rem ---------------- [2/4] GitHub CLI ----------------
gh --version >nul 2>nul
if not errorlevel 1 (
    echo [2/4] GitHub CLI: 導入済み
    goto :gh_done
)
echo [2/4] GitHub CLI をインストールしています...
winget install --id GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
set "RC=%errorlevel%"
call :add_tool_paths
gh --version >nul 2>nul
if not errorlevel 1 (
    echo [2/4] GitHub CLI: 導入しました
    goto :gh_done
)
set "TOOL=GitHub CLI"
set "TOOL_URL=https://cli.github.com/"
if not "%RC%"=="0" goto :install_failed
goto :need_restart
:gh_done

rem ---------------- [3/4] Python ----------------
call :find_python
if not errorlevel 1 (
    echo [3/4] Python: 導入済み
    goto :python_done
)
echo [3/4] Python をインストールしています...
winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
set "RC=%errorlevel%"
call :add_tool_paths
call :find_python
if not errorlevel 1 (
    echo [3/4] Python: 導入しました
    goto :python_done
)
set "TOOL=Python"
set "TOOL_URL=https://www.python.org/downloads/"
if not "%RC%"=="0" goto :install_failed
goto :need_restart
:python_done

rem ---------------- [4/4] GitHub ログイン ----------------
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

rem ---------------- mgtkit の取得 ----------------
rem pull は --ff-only にする。履歴が分かれているとマージのメッセージ入力
rem が始まり、バッチが無言で止まってしまうため
echo.
if not exist "%DEST%\.git" goto :do_clone
echo mgtkit は取得済みです。最新にしています...
git -C "%DEST%" pull --ff-only
if errorlevel 1 goto :pull_failed
goto :fetch_done

:do_clone
echo mgtkit を取得しています...
git clone "https://github.com/%REPO%.git" "%DEST%"
if errorlevel 1 goto :clone_failed
goto :fetch_done

:pull_failed
echo.
echo ※ 手元の mgtkit を最新にできませんでした。
echo    リポジトリ内のファイルを直接編集した場合などに起こります。
echo    このままアプリマネージャーを起動します。マネージャーが直接編集を
echo    退避してから最新版へ更新するので、多くの場合はこれで解消します。
echo    それでも古いままの場合は管理者に連絡してください。
:fetch_done

echo.
echo === セットアップ完了。マネージャーを起動します ===
call "%DEST%\manager\マネージャー起動.bat"
endlocal
exit /b 0

rem ============================================================
rem  ここから下は失敗時の案内とサブルーチン
rem ============================================================

:clone_failed
echo.
echo mgtkit を取得できませんでした。次を確認してください。
echo   - インターネットに接続できているか
echo   - 社内ネットワークやプロキシが github.com を遮っていないか
echo 確認してももう一度失敗する場合は、管理者に連絡してください。
pause
exit /b 1

:install_failed
echo.
echo %TOOL% を自動で導入できませんでした。考えられる原因:
echo   - 導入に管理者の許可 [UAC] が必要で、許可されなかった
echo   - 社内ネットワークが winget の配布元を遮っている
echo %TOOL_URL% から手動で導入してから、もう一度実行してください。
echo 分からない場合は管理者に連絡してください。
pause
exit /b 1

:need_restart
echo.
echo %TOOL% の導入は終わりましたが、この画面ではまだ使えません。
echo このウィンドウを閉じて、setup.bat をもう一度ダブルクリックしてください。
pause
exit /b 1

:find_python
rem 実際に動く Python を探して PY に入れる。見つからなければ errorlevel 1。
rem where を使わないのは、Windows 標準のストア誘導スタブ python.exe が
rem where では見つかってしまい、未導入を「導入済み」と誤判定するため。
rem スタブは実行すると失敗するので、動かしてみれば取り違えない。
rem (未導入の PC ではここで Microsoft Store の画面が一度開くが、
rem  判定は正しく「未導入」になる。閉じてそのまま待てばよい)
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if defined PY exit /b 0
python -c "import sys" >nul 2>nul && set "PY=python"
if defined PY exit /b 0
exit /b 1

:add_tool_paths
rem winget の直後は PATH が未反映のため、代表的な導入先を暫定的に足す。
rem 管理者権限の有無で machine / user どちらのスコープにも入りうるので
rem 両方を見る。1 行ずつ並べるのは、for でまとめると PATH がループ開始前
rem の値のまま展開され、最後の 1 つしか残らないため。
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set "PATH=%LocalAppData%\Programs\Git\cmd;%PATH%"
if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "PATH=%ProgramFiles%\GitHub CLI;%PATH%"
if exist "%LocalAppData%\Programs\GitHub CLI\gh.exe" set "PATH=%LocalAppData%\Programs\GitHub CLI;%PATH%"
if exist "%ProgramFiles%\Python311\python.exe" set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" set "PATH=%LocalAppData%\Programs\Python\Launcher;%PATH%"
exit /b 0
