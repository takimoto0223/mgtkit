@echo off
chcp 932 >nul
rem ============================================================
rem  mgtkit セットアップ (メンバー配布用)
rem  このファイルだけを新しいメンバーに渡してダブルクリックしてもらう。
rem
rem  【文字コードは Shift_JIS 固定 + chcp 932】
rem  UTF-8 + chcp 65001 だと cmd がバッチの読み取り位置を見失い、
rem  コメントや echo の断片をコマンドとして実行してしまう
rem  (実機で確認済み。manager/docs/decisions.md)。編集時は必ず
rem  Shift_JIS で保存する。日本語を増やすほど症状が出やすい。
rem
rem  【導入判定は where ではなく実行で行う】
rem  Windows は既定でストア誘導スタブ python.exe を PATH に持ち、
rem  where では未導入を「導入済み」と誤判定するため。
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

rem 前回の導入が PATH に未反映のことがあるので、判定の前に補う
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
rem --ff-only にしないと、履歴が分かれたときにマージのメッセージ入力が
rem 始まってバッチが無言で止まる
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
echo    退避してから更新し直すので、多くの場合はこれで解消します。
echo    それでも古いままの場合は管理者に連絡してください。
:fetch_done

echo.
echo === セットアップ完了。マネージャーを起動します ===
call "%DEST%\manager\マネージャー起動.bat"
endlocal
exit /b 0

rem ============ ここから下は失敗時の案内とサブルーチン ============

:clone_failed
echo.
echo mgtkit を取得できませんでした。次を確認してください。
echo   - インターネットに接続できているか
echo   - 社内ネットワークやプロキシが github.com を遮っていないか
echo それでも失敗する場合は、管理者に連絡してください。
pause
exit /b 1

:install_failed
echo.
echo %TOOL% を自動で導入できませんでした。考えられる原因:
echo   - 導入に管理者の許可 [UAC] が必要で、許可されなかった
echo   - 社内ネットワークが winget の配布元を遮っている
echo %TOOL_URL% から手動で導入してから、もう一度実行してください。
pause
exit /b 1

:need_restart
echo.
echo %TOOL% の導入は終わりましたが、この画面ではまだ使えません。
echo このウィンドウを閉じて、setup.bat をもう一度実行してください。
pause
exit /b 1

:find_python
rem 実際に動く Python を探して PY に入れる。無ければ errorlevel 1。
rem スタブは実行すると失敗するので、動かしてみれば取り違えない
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if defined PY exit /b 0
python -c "import sys" >nul 2>nul && set "PY=python"
if defined PY exit /b 0
exit /b 1

:add_tool_paths
rem winget 直後は PATH が未反映。導入先を暫定的に足す。管理者権限の
rem 有無で machine / user どちらにも入りうるので両方見る。1 行ずつ
rem 並べるのは、for だと PATH がループ開始前の値で固定されるため
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set "PATH=%LocalAppData%\Programs\Git\cmd;%PATH%"
if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "PATH=%ProgramFiles%\GitHub CLI;%PATH%"
if exist "%LocalAppData%\Programs\GitHub CLI\gh.exe" set "PATH=%LocalAppData%\Programs\GitHub CLI;%PATH%"
if exist "%ProgramFiles%\Python311\python.exe" set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" set "PATH=%LocalAppData%\Programs\Python\Launcher;%PATH%"
exit /b 0
