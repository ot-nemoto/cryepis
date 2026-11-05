# gistrap

自前のハニーポットに来たスキャンや総当たりを毎日集計し、GitHub プロフィールに Pin した Gist に
「今日の侵入者ランキング」として表示する仕組みです。VM で記録・集計し、GitHub Actions が Gist を更新します。

攻撃を受けるために置くだけで、こちらから攻撃や逆スキャンはしません。記録して集計を公開するだけです。

## 全体の流れ

```
[VM（Oracle Cloud など）]
  ├─ Cowrie        SSH(22) / Telnet(23) の受け口。偽のシェルでログイン試行とコマンドを記録
  ├─ http_server   HTTP(80) / HTTPS(443) の受け口。来たパスやヘッダーを記録して 404 を返す
  ├─ iptables LOG  全ポートへの接続の試みを記録（叩かれたポートの統計）
  └─ 1 日 1 回: aggregate.py で集計 → IP を含まない JSON を Object Storage へ
                                     （生のログ・IP は VM の外へ出さない。30 日で削除）
        ▼
[GitHub Actions（1 日 1 回）]
  └─ 集計 JSON を取得 → report.py が 6 枚のカードに整形 → Pinned Gist を更新
```

## Pin に出すもの（最大 6 枚）

| カード | 中身 |
|---|---|
| 概要 | 接続数・ユニークな IP の数・国の数・ログイン試行・HTTP・ポートの合計 |
| パスワード | SSH/Telnet で試されたパスワードのランキング |
| コマンド | ログイン後に打たれたコマンドのランキング |
| URL | HTTP/HTTPS で狙われたパスのランキング |
| ポート | 叩かれたポートのランキング |
| 国 | 攻撃元の国のランキング |

## 公開しないもの

- **IP アドレス**は集計にも Gist にも出しません（ユニークな数だけ）。攻撃元の多くは乗っ取られた無関係な機器のため。
  コマンドや URL の中に紛れた IP（`wget http://x.x.x.x/...` など）も、集計の時点で伏せ字にします。
- 生のログ（IP を含む）は VM の中だけに置き、logrotate で 35 日より古いものを消します。
- 試されたパスワードやコマンドのうち、表示に向かない語は伏せ字にし、長すぎるものは詰めます。

## ファイル

| 場所 | 役割 |
|---|---|
| `honeypot/` | VM で動かす一式（Cowrie の設定・HTTP の受け口・集計・cron・iptables）。[docs/HONEYPOT.md](docs/HONEYPOT.md) |
| `honeypot/aggregate.py` | VM で 1 日分を集計し、IP を含まない JSON を作る |
| `scripts/report.py` | 集計 JSON を読み、6 枚のカードを作って Gist を更新する（Actions 側） |
| `scripts/common.py` | GitHub API・Gist の更新・表示幅などの共通部品 |
| `.github/workflows/update-gists.yml` | 毎日 JST 7:17 に集計を取得して Gist を更新する |

集計 JSON の形式は [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md) を参照してください。

## テスト

```sh
python -m unittest discover -s tests
```

## セットアップ

1. VM を立ててハニーポットを動かす（[docs/HONEYPOT.md](docs/HONEYPOT.md)）。
2. gist スコープの Classic PAT を Secret `GIST_PAT` に登録する。
3. **Actions** → *Update honeypot gists* → **Run workflow** で `create_gists` にチェックを入れて実行する。
   6 枚の Gist が作られ、ID の一覧が実行結果の Summary に出る。
4. その一覧を Repository variable `GIST_IDS`（JSON）に登録する。
5. 集計 JSON を読む URL を Secret `HONEYPOT_DATA_URL` に登録する。
6. チェックを外して **Run workflow**（または翌朝の定期実行）で更新し、6 枚の Gist を Pin する。
