# セットアップ手順

## 1. Classic PAT を作る

Gist は Fine-grained PAT に対応していないため Classic PAT を使います。

1. GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token
2. スコープは **`gist`** のみ。Expiration は任意（切れる前に更新が必要）
3. このリポジトリ → **Settings** → **Secrets and variables** → **Actions** で Secret **`GIST_PAT`** に登録する

## 2. もののふの冒険（テキスト RPG）

1. **Actions** → *New game (add a hero)* → **Run workflow** で、もののふの名前を入れて実行する。
   もののふ管理シート `.state/heroes.json` に登録される（Gist は作らない。冒険はまだ進まない。詳細はサイトのもののふのページで見る）
2. 本番の冒険は次回の定期実行（JST 7:17）から進む。すぐ進めたいときは *Advance hero adventure (text RPG)* を **Run workflow** で手動実行する
3. 最初の毎日の実行で**番付の Gist**（`hero-ranking.txt`）が作られる（URL は実行結果の **Summary** と `.state/board.json`）。
   プロフィールの **Customize your pins** でこれを Pin する（Pin は repo と合わせて 6 件まで）
4. もののふを増やすときは 1 を繰り返す（現役は最大 30 人）。やめさせるときは *Retire hero* にもののふの ID（`h1` など）か名前を入れて実行する

手元で試す場合は `python scripts/rpg.py --new-game 名前`（もののふの登録には PAT は要らない）。

| 種類 | 名前 | 値 |
|---|---|---|
| Secret | `GIST_PAT` | 手順 1 の Classic PAT |

本番の番付 Gist の ID は master の `.state/board.json` に記録される。
もののふがいない間、毎日の実行は何もしない（keepalive だけコミットする）。

`.state/` の記録は github-actions[bot] 名義で 対象のブランチに直接コミットされます（PR は通しません）。
ブランチ保護を設定する場合は、日次進行・登録・引退で使う Actions の直接 push を許可してください。

## 3. 開発と本番リリース

- デフォルトブランチは本番の `master`。初回はこの仕組みをマージした master から `develop` を作る。作成時点のセーブを初期値として、以降はそれぞれのブランチで進む。
- 機能の作業ブランチは `develop` から切り、PR の送り先も `develop` にする。ゲームを手動で進める・もののふを登録する・引退させるときは、Actions の **Use workflow from** で `master`、登録・引退は入力の **変更するゲーム** で対象を選ぶ。手動で進めるときは本番の *Advance hero adventure (text RPG)* と開発の *Advance development game* を使い分ける。
- 開発用は本番と同じく JST 7:17 に 1 日 1 回起動する。実行されなかった日は、次の実行で最大 7 日分さかのぼって進める（#299）。
- 番付 Gist は本番のみ。開発側は `python scripts/rpg.py --no-gist` でセーブを保存し、番付は開発版サイトで確認する。
- 本番と開発のセーブが混ざらないように、本番へは毎回 `python tools/release.py` でリリースブランチを作る。最新の master を元に develop を取り込み、`.state/` は master の内容を保持し、`VERSION` の末尾を 1 つ上げる。コードの競合を解決し直す場合は、リリース準備時のエラーメッセージに従う。
- リリースブランチから `master` への PR を作り、公開対象の Issue ごとに `Closes #番号` を書く。CI は PR のマージ結果で `.state/` の差分とバージョンを確認する。プレビュー確認後は **Create a merge commit** で取り込む。日次処理ではバージョンを更新しない。

## 4. Cloudflare Pages への公開

Cloudflare Pages で Direct Upload のプロジェクトを作成し、本番ブランチを `master` にする。同じプロジェクトに本番・開発・本番 PR の生成済み HTML を公開する。API トークンは Cloudflare の対象アカウントに絞り、**Account / Cloudflare Pages / Edit** 権限を付ける。

| GitHub Actions の種類 | 名前 | 値 |
|---|---|---|
| Secret | `CLOUDFLARE_API_TOKEN` | Cloudflare の API トークン |
| Variable または Secret | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare のアカウント ID |
| Variable | `CLOUDFLARE_PAGES_PROJECT` | Direct Upload プロジェクトの実際の名前 |
| Variable | `PRODUCTION_SITE_URL` | 切り替え確認後に設定する Cloudflare の本番 HTTPS URL |

`CLOUDFLARE_PAGES_PROJECT` を設定すると、`pages.yml` が Cloudflare にも配信する。GitHub Actions の実行結果の **Summary** に公開 URL が載る。本番 PR のプレビューは、GitHub が作るマージ結果に含まれる本番セーブの写しで生成する。PR の番号が 123 なら、プレビュー名は `pr-123`。

本番・開発・本番 PR のプレビューが表示できることを確認してから `PRODUCTION_SITE_URL` を登録する。次回の公開で旧 GitHub Pages が移転案内になり、次回の本番実行で番付 Gist のリンクも更新される。切り替えを戻す場合は `PRODUCTION_SITE_URL` を消して、master で公開を再実行する。

## トラブルシューティング

| 症状 | 原因 |
|---|---|
| `Gist API エラー 403/404` | PAT の `gist` スコープ不足・期限切れ、または Gist ID の誤り |
| *Commit state* ステップで push が失敗する | master のブランチ保護で bot の直接 push が拒否されている |
| 警告 `…を進められませんでした: …`（ジョブが失敗する） | そのもののふだけの失敗（状態ファイルが読めないなど）。ほかのもののふは進んでいる。警告の中身を確認して、もう一度実行する |
| 警告 `番付の Gist を更新できませんでした: …`（ジョブが失敗する） | PAT の期限切れ・スコープ不足など。もののふの状態は保存されている。PAT を確認して、もう一度実行する |
| 手動実行（*New game* / *Retire hero*）が `Cancelled` になる | 同じ `concurrency` グループ（毎日の実行を含む）で待てるのは 1 件だけで、さらに後から起動した実行が先の待機中の実行を置き換える。1 件終わってから次を実行する（取り消された分はもう一度実行する） |
| *New game* が `もののふは最大 30 人です` で失敗する | 現役のもののふが上限。*Retire hero* で引退させてから実行する |
| 毎日の実行が `GIST_PAT を環境変数で指定してください` で失敗する | Secret `GIST_PAT` が未登録 |
| 警告 `番付の Gist（…）が見つからないので、新しく作ります` | 番付の Gist が消された。新しい Gist が作られるので Pin し直す |
| `….json が JSON として読めません` / `読めません`（ジョブが失敗する） | マージ衝突などでファイルが壊れた。master の履歴から直前の正しい内容に戻す |
| workflow が動かない | 60 日無活動で停止。Actions タブで *Enable workflow*（keepalive で通常は防げる） |
| 実行が数十分遅れる | Actions の cron は遅延することがある（仕様） |
