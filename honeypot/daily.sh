#!/usr/bin/env bash
# 1 日 1 回 cron で実行する。前日分を集計し、集計 JSON（IP を含まない）だけを Object Storage へ送る。
# 生のログ・IP は VM の外へ出さない。GitHub のトークンは VM に置かない。
#
# cron の例（毎日 00:10 UTC）:
#   10 0 * * * /opt/honeypot/daily.sh >> /var/log/honeypot-daily.log 2>&1
#
# 書き込み専用の事前認証 URL（PAR）を環境変数 PAR_PUT_URL に入れておく（末尾にオブジェクト名を足す）。
set -euo pipefail
cd "$(dirname "$0")"

DATE="$(date -u +%F)"
OUT="/tmp/honeypot-${DATE}.json"

python3 aggregate.py --date "$DATE" --out "$OUT"

# 最新分（latest.json）として上書きアップロード。PAR_PUT_URL は末尾が "/" の PAR を想定
if [ -n "${PAR_PUT_URL:-}" ]; then
  curl -fsS -X PUT "${PAR_PUT_URL}latest.json" --data-binary "@${OUT}" -H "Content-Type: application/json"
  curl -fsS -X PUT "${PAR_PUT_URL}daily/${DATE}.json" --data-binary "@${OUT}" -H "Content-Type: application/json"
fi

# 生のログは 30 日より古いものを消す（Cowrie / HTTP のログを置いたパスに合わせて調整）
find /opt/honeypot -name "*.json" -path "*log*" -mtime +30 -delete 2>/dev/null || true
rm -f "$OUT"
