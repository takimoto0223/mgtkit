@echo off
chcp 932 >nul
rem ============================================================
rem  mgtkit 引っ越しの試験用 (管理者だけが使う。メンバーには配らない)
rem
rem  使い方:  setup-test.bat <ブランチ名>
rem
rem  ふだんの setup.bat を、次の 3 点だけ差し替えて呼ぶ:
rem    - 置き場所を C:\mgtkit_appmanager_test にする (本番と別)
rem    - 取得元を main ではなく指定したブランチにする
rem      (main に入れると全員の PC に届いてしまうため)
rem    - 以前のフォルダの片付けを予約しない (--no-cleanup)
rem
rem  以前のフォルダは読むだけなので、試したあとは
rem  C:\mgtkit_appmanager_test を消せば元どおりになる。
rem
rem  何をするかだけ見たいときは、第 2 引数に --dry-run を渡す。
rem  文字コードは Shift_JIS 固定 (setup.bat の冒頭を参照)
rem ============================================================
setlocal
title mgtkit セットアップ (試験)

if "%~1"=="" goto :need_branch
set "MGTKIT_BRANCH=%~1"
set "MGTKIT_HOME=%SystemDrive%\mgtkit_appmanager_test"
set "MGTKIT_MIGRATE_OPTS=--no-cleanup"
if not "%~2"=="" set "MGTKIT_MIGRATE_OPTS=--no-cleanup %~2"

echo === 試験モード ===
echo   ブランチ  : %MGTKIT_BRANCH%
echo   置き場所  : %MGTKIT_HOME%
echo   引っ越し  : %MGTKIT_MIGRATE_OPTS%
echo 以前のフォルダは読むだけで、消しません。
echo.
call "%~dp0setup.bat"
endlocal
exit /b 0

:need_branch
echo 試すブランチ名を指定してください。
echo   例) setup-test.bat claude/folder-layout-xxxx
pause
exit /b 1
