# 集計 JSON の形式

VM 側の `honeypot/aggregate.py` が 1 日 1 回作り、Object Storage に置く JSON です。
GitHub Actions 側の `scripts/report.py` はこれを読んでカードを作ります。
**IP アドレス・生のログは含みません。** 動作確認用の例は [`tests/sample_data.json`](../tests/sample_data.json)。

```json
{
  "date": "2026-11-05",
  "generated_at": "2026-11-06T00:10:03+00:00",
  "totals": {
    "sessions": 1284,
    "unique_ips": 312,
    "countries": 41,
    "login_attempts": 9821,
    "http_requests": 5120,
    "port_hits": 20433,
    "downloads_attempted": 37
  },
  "top_usernames":  [{"v": "root", "n": 4120}],
  "top_passwords":  [{"v": "123456", "n": 611}],
  "top_commands":   [{"v": "uname -a", "n": 210}],
  "top_http_paths": [{"v": "/.env", "n": 402}],
  "top_ports":      [{"v": 22, "proto": "tcp", "n": 8120}],
  "top_countries":  [{"v": "US", "n": 520}],
  "top_networks":   [{"v": "AS14061 DigitalOcean", "n": 230}]
}
```

| キー | 中身 |
|---|---|
| `date` | 集計の対象日（UTC の `YYYY-MM-DD`） |
| `generated_at` | 作成時刻（UTC） |
| `totals` | 各種の合計。`unique_ips` は数だけ（IP そのものは持たない） |
| `top_*` | ランキング。各要素は `{"v": 値, "n": 件数}`。`top_ports` だけ `proto`（tcp/udp）も持つ |

- `date` の 1 日分だけを数える（`aggregate.py` が各ログを日付で絞る。ログ自体は累積でもよい）。
- ランキングは件数の多い順で、各 `aggregate.py` の `TOP_N`（既定 10）件まで。カードには上位 4 件を出す。
- 値（`v`）は VM 側で、IP アドレスと表示に向かない語を伏せ字にし、制御文字を除き、長すぎるものを詰めてある
  （NFKC 正規化のうえ判定する）。Actions 側（`report.py`）でも、表示の直前に同じ防御をかける。
- `top_usernames` と `top_networks` は JSON には入れるが、今のカード（6 枚）には出していない（将来用）。
