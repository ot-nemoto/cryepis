# VM のセットアップ手順

Oracle Cloud の Always Free の VM（Ubuntu）を例に、ハニーポットを動かすまでの手順です。
目的は、来たスキャンや総当たりを記録して集計を公開することです。攻撃や逆スキャンはしません。

## 用意するもの

- Oracle Cloud の VM（Ubuntu 22.04 以降、Always Free の範囲）
- Object Storage のバケット 1 つ（非公開）と、事前認証リクエスト（PAR）
  - 書き込み専用の PAR（VM からアップロードする用）
  - 読み取り専用の PAR（GitHub Actions が読む用。Secret `HONEYPOT_DATA_URL` に入れる）
- 任意で DB-IP Lite の mmdb（国・ネットワークの変換用。無くても動く）

## 1. 本物の SSH を避難させる

ハニーポットが 22 番を使うので、自分がログインする SSH を別ポートに移します。

```sh
sudo sed -i 's/^#\?Port .*/Port 22222/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

- OCI のセキュリティリスト（またはネットワークセキュリティグループ）で **22222 を自分の IP からだけ**開ける。
- 鍵認証のみにする（`PasswordAuthentication no`）。
- **以降、自分のログインは `ssh -p 22222` を使う。**

## 2. ポートを開ける

OCI 側（セキュリティリスト）と VM 側（iptables）の両方で開ける必要があります。ここがつまずきやすい点です。

- OCI のセキュリティリスト: 22・23・80・443 を 0.0.0.0/0 から許可（22222 は自分の IP だけ）。
- VM 側: Ubuntu は既定で ufw/iptables がほぼ閉じているので、上記ポートを許可する。

## 3. Docker とハニーポットを動かす

```sh
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 iptables-persistent python3
sudo mkdir -p /opt/honeypot && sudo chown "$USER" /opt/honeypot
# このリポジトリの honeypot/ を /opt/honeypot に置く（git clone か scp）
cd /opt/honeypot
# ログの出力先を作り、コンテナの実行ユーザー（uid 1000）が書けるようにする
mkdir -p cowrie/var http
sudo chown -R 1000:1000 cowrie/var http
docker compose up -d
```

ログは bind mount でホストの `cowrie/var/log/cowrie/cowrie.json` と `http/requests.log` に出ます
（`aggregate.py` の既定の読み取りパスと同じ）。Cowrie はホスト鍵や pid を `cowrie/var` に書くので、
このディレクトリは書き込み可能にしておきます（compose では Cowrie のルートを読み取り専用にしていません）。

## 4. ポート転送と記録の設定

```sh
sudo bash /opt/honeypot/setup-iptables.sh
sudo netfilter-persistent save   # 再起動後も残す
```

- 22→2222（Cowrie SSH）、23→2223（Cowrie Telnet）に転送されます。
- 全ポートへの接続の試みが `/var/log/kern.log` に `HONEYPOT` の目印付きで記録されます。

### HTTPS（443）も記録する場合

自己署名の証明書を作り、`http_server.py` を 443 でもう 1 つ動かします（IP 宛てのスキャンは証明書を確かめずに来ることが多い）。

```sh
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -subj "/CN=srv01" -keyout /opt/honeypot/http/key.pem -out /opt/honeypot/http/cert.pem
```

`docker-compose.yml` に 443 用のサービスを足し、`--cert`/`--key`/`--port 443` を渡します。

## 5. 生のログの保存量を抑える（logrotate）

Cowrie と HTTP のログは単一ファイルに追記され続けるので、logrotate で日次ローテーション・35 日保持にします。

```sh
sudo cp /opt/honeypot/honeypot.logrotate /etc/logrotate.d/honeypot
```

`aggregate.py` は本体と、ローテーション後の `*.1` の両方を読むので、集計がちょうど日付の変わり目に当たっても
前日分を取りこぼしません。`/var/log/kern.log`（iptables LOG の出力先）は OS 標準の logrotate がすでに回しています。

## 6. 1 日 1 回の集計とアップロード

```sh
sudo crontab -e
```

```
PAR_PUT_URL=https://<書き込み専用の PAR の URL。末尾は / >
10 0 * * * /opt/honeypot/daily.sh >> /var/log/honeypot-daily.log 2>&1
```

- `daily.sh` は**前日（UTC）**分だけを集計し、`latest.json` と `daily/<日付>.json` を Object Storage にアップロードします。
  `aggregate.py` が各ログを日付で絞るので、ログが累積でもカードの数字は 1 日分になります。
- 生のログ（IP を含む）は VM の中だけに残り、logrotate で 35 日より古いものは消えます。

## 7. GitHub 側

- Secret `HONEYPOT_DATA_URL` に、`latest.json` を指す**読み取り専用の PAR の URL**を入れる。
- 以降は README の「セットアップ」の手順（Gist を作って `GIST_IDS` を登録）に進む。

## 安全のための約束

- **VM に GitHub のトークンなどの秘密情報を置かない。** VM は乗っ取られる前提で考える。
- Cowrie はファイルの取得と転送を無効にしてある（マルウェアを保存せず、踏み台にさせない）。
- 偽の設定ファイルなどに、実在するサービスの本物のキーを置かない。
- Object Storage のバケットは非公開にし、ライフサイクルで `daily/` を 90 日後に削除する。
- OCI の予算アラート（例: 月 1 ドル）を設定する。無料トライアルのままなら、超過は課金されず止まるだけ。
- OS の自動更新（`unattended-upgrades`）を有効にする。
