# セットアップ手順

## 1. Classic PAT を作る

Gist は Fine-grained PAT に対応していないため Classic PAT を使います。

1. GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token
2. スコープは **`gist`** のみ。Expiration は任意（切れる前に更新が必要）
3. このリポジトリ → **Settings** → **Secrets and variables** → **Actions** で Secret **`GIST_PAT`** に登録する

## 2. 勇者の冒険（テキスト RPG）

1. **Actions** → *Advance hero adventure (text RPG)* → **Run workflow** で、**`create_gist` にチェックを入れて**実行する。
   冒険前の勇者のカードを中身にした public Gist（`hero-adventure.txt`）が作られ、実行結果の **Summary** に ID と URL が出る
   （冒険はまだ進まない）
2. その ID を Repository variable **`GIST_ID_RPG`** に登録する
3. 任意で Variable **`RPG_USER`** に、コントリビューションを数える GitHub ユーザー名を登録する（既定はリポジトリの持ち主。
   持ち主が Organization のときは必ず登録する）
4. もう一度 **Run workflow**（`create_gist` のチェックは外す）で初日を進め、
   プロフィールの **Customize your pins** で Gist を Pin する

手元で Gist を作る場合は `GIST_PAT=<手順 1 の PAT> python scripts/rpg.py --create-gist`（ID と URL が表示される）。
自分で作った Gist を使ってもよい（public・ファイルは 1 つ。ファイル名は初回の更新で `hero-adventure.txt` へ自動でリネームされる。
Pinned カードには名前順で先頭のファイルが表示されるため、ファイルが複数あると別のファイルが出ることがある）。
`GIST_ID_RPG` の登録は自動ではできない（workflow 標準の `GITHUB_TOKEN` では Repository variable を書けないため）。

| 種類 | 名前 | 値 |
|---|---|---|
| Secret | `GIST_PAT` | 手順 1 の Classic PAT |
| Variable | `GIST_ID_RPG` | RPG 用 Gist の ID |
| Variable | `RPG_USER`（任意） | コントリビューションを数える GitHub ユーザー名 |

`GIST_ID_RPG` が未設定のあいだは、workflow の Advance RPG ステップは実行されない（`.state/rpg.json` も作られない）。

前日のコントリビューション数は GitHub の GraphQL で取る。`GIST_PAT` で取り、取れなければ workflow 標準の `GITHUB_TOKEN` で取り直す。
非公開リポジトリでの活動は、プロフィールの設定（Private contributions を表示するか）によって数に入らないことがある。

`.state/` の記録は github-actions[bot] 名義で master に直接コミットされます（PR は通しません）。
master にブランチ保護（PR 必須など）を設定すると、このコミットが失敗するので注意してください。

## トラブルシューティング

| 症状 | 原因 |
|---|---|
| `Gist API エラー 403/404`（ジョブが失敗する） | PAT の `gist` スコープ不足・期限切れ、または Gist ID の誤り |
| *Commit state* ステップで push が失敗する | master のブランチ保護で bot の直接 push が拒否されている |
| 警告 `コントリビューション数を取得できません。… から先は進めません` | GraphQL の取得に失敗した（トークンの権限や GitHub の不調。一時的なエラーは再試行している）。次の実行で、その日からさかのぼって進む（最大 7 日分） |
| *Create gist* が `GIST_ID_RPG が設定済み` で失敗する | すでに Gist がある。作り直すなら `GIST_ID_RPG` を消してから実行する |
| `.state/rpg.json が JSON として読めません`（ジョブが失敗する） | マージ衝突などでファイルが壊れた。master の履歴から直前の正しい内容に戻す |
| workflow が動かない | 60 日無活動で停止。Actions タブで *Enable workflow*（keepalive で通常は防げる） |
| 実行が数十分遅れる | Actions の cron は遅延することがある（仕様） |
