#!/usr/bin/env bash
# 1 日 1 回 cron で実行する。前日分を集計し、集計 JSON（IP を含まない）だけを Object Storage へ送る。
# 生のログ・IP は VM の外へ出さない。GitHub のトークンは VM に置かない。
#
# cron の例（毎日 00:10 UTC。前日分がそろってから）:
#   10 0 * * * /opt/honeypot/daily.sh >> /var/log/honeypot-daily.log 2>&1
#
# 書き込み専用の事前認証 URL（PAR）を環境変数 PAR_PUT_URL に入れておく（末尾は "/"）。
# 生のログの保存量は logrotate で抑える（docs/HONEYPOT.md の honeypot.logrotate）。
set -euo pipefail
cd "$(dirname "$0")"

DATE="$(date -u -d 'yesterday' +%F)"  # 前日（UTC）
OUT="/tmp/honeypot-${DATE}.json"

# aggregate.py が --date で前日分だけを数える（ログは累積だが日付で絞る）
python3 aggregate.py --date "$DATE" --out "$OUT"

# 最新分（latest.json）と、日付ごとの控えをアップロード。PAR_PUT_URL は末尾が "/" の PAR を想定
if [ -n "${PAR_PUT_URL:-}" ]; then
  curl -fsS -X PUT "${PAR_PUT_URL}latest.json" --data-binary "@${OUT}" -H "Content-Type: application/json"
  curl -fsS -X PUT "${PAR_PUT_URL}daily/${DATE}.json" --data-binary "@${OUT}" -H "Content-Type: application/json"
fi

rm -f "$OUT"
