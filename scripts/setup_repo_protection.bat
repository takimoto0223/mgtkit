@echo off
rem main ブランチ保護のセットアップ (管理者が一度だけ実行する / Windows 用)
rem
rem 前提: gh CLI がインストール済みで `gh auth login` 済みであること。
rem 設定値は リポジトリルートの config.json (branch_protection) から読む。
rem 承認必要数を変更する場合は config.json を編集して本スクリプトを再実行する。
rem
rem 注意: 個人アカウントの private リポジトリではブランチ保護に GitHub Pro 等の
rem       有料プランが必要 (public リポジトリなら無料)。403/422 が返る場合は
rem       プラン・リポジトリの公開設定を確認すること。
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

where gh >nul 2>nul
if errorlevel 1 (
    echo エラー: gh CLI が見つかりません。https://cli.github.com/ から導入してください。
    exit /b 1
)

set "PY=python"
where py >nul 2>nul && set "PY=py"

set "BODY=%TEMP%\mgtkit_protection.json"
%PY% -c "import json;c=json.load(open('config.json'));bp=c['branch_protection'];json.dump({'required_status_checks':{'strict':bp['strict_status_checks'],'contexts':bp['required_status_checks']},'enforce_admins':bp['enforce_admins'],'required_pull_request_reviews':{'required_approving_review_count':bp['required_approvals'],'dismiss_stale_reviews':True},'restrictions':None,'allow_force_pushes':False,'allow_deletions':False},open(r'%BODY%','w'))"
if errorlevel 1 (
    echo エラー: config.json の読み込みに失敗しました。
    exit /b 1
)

for /f "usebackq delims=" %%R in (`%PY% -c "import json;print(json.load(open('config.json'))['repo'])"`) do set "REPO=%%R"
for /f "usebackq delims=" %%B in (`%PY% -c "import json;print(json.load(open('config.json'))['base_branch'])"`) do set "BRANCH=%%B"

echo ブランチ保護を適用します: %REPO%@%BRANCH%
gh api -X PUT "repos/%REPO%/branches/%BRANCH%/protection" --input "%BODY%"
if errorlevel 1 (
    echo.
    echo エラー: ブランチ保護の適用に失敗しました。
    echo   - private 個人リポジトリの場合、GitHub Pro 等の有料プランが必要です。
    echo   - リポジトリの管理者権限があるか確認してください。
    del "%BODY%" >nul 2>nul
    exit /b 1
)
del "%BODY%" >nul 2>nul
echo 完了。設定内容の確認: gh api repos/%REPO%/branches/%BRANCH%/protection
endlocal
