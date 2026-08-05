#!/usr/bin/env bash
# main ブランチ保護のセットアップ (管理者が一度だけ実行する)
#
# 前提: gh CLI がインストール済みで `gh auth login` 済みであること。
# 設定値は リポジトリルートの config.json (branch_protection) から読む。
# 承認必要数を変更する場合は config.json を編集して本スクリプトを再実行する。
#
# 注意: 個人アカウントの private リポジトリではブランチ保護に GitHub Pro 等の
#       有料プランが必要 (public リポジトリなら無料)。403/422 が返る場合は
#       プラン・リポジトリの公開設定を確認すること。
set -euo pipefail
cd "$(dirname "$0")/.."

CFG=config.json
if ! command -v gh >/dev/null 2>&1; then
    echo "エラー: gh CLI が見つかりません。https://cli.github.com/ から導入してください。" >&2
    exit 1
fi

REPO=$(python3 -c "import json;print(json.load(open('$CFG'))['repo'])")
BRANCH=$(python3 -c "import json;print(json.load(open('$CFG'))['base_branch'])")
APPROVALS=$(python3 -c "import json;print(json.load(open('$CFG'))['branch_protection']['required_approvals'])")
CHECKS=$(python3 -c "import json;print(json.dumps(json.load(open('$CFG'))['branch_protection']['required_status_checks']))")
STRICT=$(python3 -c "import json;print(str(json.load(open('$CFG'))['branch_protection']['strict_status_checks']).lower())")
ENFORCE=$(python3 -c "import json;print(str(json.load(open('$CFG'))['branch_protection']['enforce_admins']).lower())")

echo "ブランチ保護を適用します: ${REPO}@${BRANCH} (承認 ${APPROVALS} 名, checks=${CHECKS})"

if ! gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" --input - <<EOF
{
  "required_status_checks": { "strict": ${STRICT}, "contexts": ${CHECKS} },
  "enforce_admins": ${ENFORCE},
  "required_pull_request_reviews": {
    "required_approving_review_count": ${APPROVALS},
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
then
    echo "" >&2
    echo "エラー: ブランチ保護の適用に失敗しました。" >&2
    echo "  - private 個人リポジトリの場合、GitHub Pro 等の有料プランが必要です。" >&2
    echo "  - リポジトリの管理者権限があるか確認してください。" >&2
    exit 1
fi

echo "完了。設定内容の確認: gh api repos/${REPO}/branches/${BRANCH}/protection"
