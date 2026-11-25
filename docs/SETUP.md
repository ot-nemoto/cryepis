# セットアップ手順

## 1. Classic PAT を作る

Gist は Fine-grained PAT に対応していないため Classic PAT を使います。

1. GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token
2. スコープは **`gist`** のみ。Expiration は任意（切れる前に更新が必要）
3. このリポジトリ → **Settings** → **Secrets and variables** → **Actions** で Secret **`GIST_PAT`** に登録する

## 2. もののふの冒険（テキスト RPG）

1. **Actions** → *New game (add a hero)* → **Run workflow** で、もののふの名前を入れて実行する。
   もののふ管理シート `.state/heroes.json` に登録される（Gist は作らない。冒険はまだ進まない。詳細はサイトのもののふのページで見る）
2. 冒険は次回の毎日の実行（JST 7:17）から進む。すぐ進めたいときは *Advance hero adventure (text RPG)* を **Run workflow** で手動実行する
3. 最初の毎日の実行で**ランキングの Gist**（`hero-ranking.txt`）が作られる（URL は実行結果の **Summary** と `.state/board.json`）。
   プロフィールの **Customize your pins** でこれを Pin する（Pin は repo と合わせて 6 件まで）
4. もののふを増やすときは 1 を繰り返す（現役は最大 30 人）。やめさせるときは *Retire hero* にもののふの ID（`h1` など）か名前を入れて実行する

手元で試す場合は `python scripts/rpg.py --new-game 名前`（もののふの登録には PAT は要らない）。

| 種類 | 名前 | 値 |
|---|---|---|
| Secret | `GIST_PAT` | 手順 1 の Classic PAT |

Repository variable は使わない（ランキングの Gist の ID は `.state/board.json` に記録される）。
もののふがいない間、毎日の実行は何もしない（keepalive だけコミットする）。

`.state/` の記録は github-actions[bot] 名義で master に直接コミットされます（PR は通しません）。
master にブランチ保護（PR 必須など）を設定すると、このコミットが失敗するので注意してください。

## トラブルシューティング

| 症状 | 原因 |
|---|---|
| `Gist API エラー 403/404` | PAT の `gist` スコープ不足・期限切れ、または Gist ID の誤り |
| *Commit state* ステップで push が失敗する | master のブランチ保護で bot の直接 push が拒否されている |
| 警告 `…を進められませんでした: …`（ジョブが失敗する） | そのもののふだけの失敗（状態ファイルが読めないなど）。ほかのもののふは進んでいる。警告の中身を確認して、もう一度実行する |
| 警告 `ランキングの Gist を更新できませんでした: …`（ジョブが失敗する） | PAT の期限切れ・スコープ不足など。もののふの状態は保存されている。PAT を確認して、もう一度実行する |
| 手動実行（*New game* / *Retire hero*）が `Cancelled` になる | 同じ `concurrency` グループ（毎日の実行を含む）で待てるのは 1 件だけで、さらに後から起動した実行が先の待機中の実行を置き換える。1 件終わってから次を実行する（取り消された分はもう一度実行する） |
| *New game* が `もののふは最大 30 人です` で失敗する | 現役のもののふが上限。*Retire hero* で引退させてから実行する |
| 毎日の実行が `GIST_PAT を環境変数で指定してください` で失敗する | Secret `GIST_PAT` が未登録 |
| 警告 `ランキングの Gist（…）が見つからないので、新しく作ります` | ランキングの Gist が消された。新しい Gist が作られるので Pin し直す |
| `….json が JSON として読めません` / `読めません`（ジョブが失敗する） | マージ衝突などでファイルが壊れた。master の履歴から直前の正しい内容に戻す |
| workflow が動かない | 60 日無活動で停止。Actions タブで *Enable workflow*（keepalive で通常は防げる） |
| 実行が数十分遅れる | Actions の cron は遅延することがある（仕様） |
