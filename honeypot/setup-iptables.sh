#!/usr/bin/env bash
# 22->2222, 23->2223 に転送し、全ポートへの新規の接続の試みを LOG に出す（集計用）。
# 本物の SSH は 22222 に移してある前提（docs/HONEYPOT.md）。root で 1 回実行する。
set -euo pipefail

# 攻撃者には 22/23 に見せ、Cowrie の 2222/2223 へ回す
iptables -t nat -A PREROUTING -p tcp --dport 22 -j REDIRECT --to-port 2222
iptables -t nat -A PREROUTING -p tcp --dport 23 -j REDIRECT --to-port 2223

# 新規接続の試み（SYN）と UDP を LOG に出す。--log-prefix はログの目印。頻度を絞って溢れを防ぐ
iptables -A INPUT -p tcp --syn -m limit --limit 60/minute --limit-burst 100 \
  -j LOG --log-prefix "HONEYPOT " --log-level 6
iptables -A INPUT -p udp -m limit --limit 30/minute --limit-burst 50 \
  -j LOG --log-prefix "HONEYPOT " --log-level 6

echo "適用しました。永続化するには netfilter-persistent (iptables-persistent) を使ってください。"
