# データの流れ

どの workflow（とスクリプト）が、どのファイルを読み書きし、それが何に使われるかをまとめます。

## 全体図

```mermaid
flowchart LR
    subgraph daily["master-adventure.yml（毎日 JST 7:17）"]
        direction TB
        test["Test<br/>（unittest）"]
        rpg["rpg.py（world.py・game.py・store.py・view.py）<br/>現役のもののふを全員 1 日進める"]
        commit["Commit state<br/>（master に直接コミット）"]
        test ==> rpg ==> commit
    end

    subgraph manual["手動実行"]
        newgame["new-game.yml<br/>もののふを追加"]
        retire["retire-hero.yml<br/>もののふを引退"]
    end

    subgraph state[".state/（master にコミット）"]
        heroes[("heroes.json<br/>もののふ管理シート")]
        board[("board.json<br/>番付の Gist の ID")]
        heroState[("heroes/&lt;id&gt;.json<br/>もののふごとの状態・持ち物・日誌・シーズンの記録")]
        lastRun[("last-run<br/>keepalive 用の日付")]
    end

    world[("world/*.json<br/>シーズン・MAP・敵・武具・依頼")]
    site["サイト（GitHub Pages）<br/>pages.yml の website.py が書き出す<br/>地図・もののふの詳細など"]
    ranking["番付の Gist<br/>（Pin する）"]

    world --> rpg
    heroes -->|"現役のもののふ"| rpg
    heroState <-->|"前日の状態を読み、今日の分を書く"| rpg
    heroState -->|"冒険の様子"| site
    world --> site
    rpg -->|"シーズンの順位"| ranking
    board <-->|"Gist の ID（なければ作って記録）"| rpg
    newgame -->|"もののふを登録"| heroes
    newgame -->|"初期状態"| heroState
    retire -->|"active を false に"| heroes
    commit -->|"日付を更新"| lastRun
```

- 太線（`master-adventure.yml` の中）: ステップの実行順。各ステップの実行条件は下の「毎日の workflow の処理順」を参照
- 実線: データの読み書き
- 手動実行の workflow は毎日の workflow と同じ `concurrency` グループで、`.state/` への書き込みが重ならないようにしている
- Variable `R2_STATE_BRANCHES` に入れたゲームは、`.state/` をブランチにコミットせず Cloudflare R2 に置く（下の「状態を R2 に置くゲーム」）

## ファイルごとの役割

| ファイル | 書き込み元 | 中身 | 使い道 |
|---|---|---|---|
| `world/*.json` | 人（手で編集） | テキスト RPG の世界（シーズン・MAP・敵・武具・口入れ屋の依頼）。形式は README の「世界を広げる」 | `rpg.py` の入力（Test で検証してから使う） |
| `.state/heroes.json` | `rpg.py`（New game / Retire hero） | もののふの一覧（ID・名前・作成日・現役かどうか）。現役は最大 30 人 | 毎日の更新の対象 |
| `.state/heroes/<id>.json` | `rpg.py` | もののふの状態（場所・HP・レベル・依頼など）、持ち物、直近 30 日の日誌、シーズンの記録 | 翌日の続きと、Gist の表示 |
| `.state/board.json` | `rpg.py`（最初の毎日の実行） | 番付の Gist の ID と URL | 毎日更新する番付の Gist |
| `.state/guild.json` | `rpg.py`（毎日の実行） | 町ごとの口入れ屋の高札（日をまたいで残る。受付中・受注中・達成・失敗） | もののふが依頼を受ける（早い者勝ち）。サイトの口入れ屋のページで、受付の残り日数と受けたもののふを出す |
| サイト（GitHub Pages） | `website.py`（`pages.yml`） | 地図・番付・口入れ屋の今日の依頼・もののふの詳細の HTML（`.state/` と `world/` から書き出す。コミットしない） | ブラウザで見る。番付の Gist からリンクする |
| `.state/last-run` | Commit state ステップ | 最終実行日（JST） | 60 日無活動で scheduled workflow が止まるのを防ぐ（keepalive） |

`.state/` の各ファイルの JSON 構成・項目の意味・例は [STATE_FORMAT.md](STATE_FORMAT.md) を参照してください。

保持期間:
- `heroes/<id>.json` は毎日上書きします（日誌は直近 30 日分、シーズンの記録と持ち物は無制限）。引退したもののふのファイルはそのまま残ります。
- `heroes.json` はもののふの追加・引退のときだけ書き換えます。
- `board.json` は番付の Gist を作ったときに 1 回だけ書きます。
- `last-run` は毎回上書きします。

## 毎日の workflow の処理順

```mermaid
sequenceDiagram
    autonumber
    participant WF as master-adventure.yml
    participant RP as rpg.py
    participant G as 番付の Gist
    participant S as .state/*
    participant M as master

    WF->>WF: Test（unittest）
    Note over WF,RP: Test が失敗したら rpg.py はスキップ。もののふがいなければ何もしない
    WF->>RP: 実行
    RP->>S: heroes.json から現役のもののふを読む
    loop 現役のもののふごと（失敗はもののふごとに隔離）
        RP->>S: heroes/<id>.json を 1 日分進める（行動回数は毎日同じ、出来事は乱数。シーズンが変わったら持ち物以外をリセット。処理済みなら進めない。実行されなかった日があればさかのぼって進める）
    end
    RP->>G: 番付の Gist を更新（board.json がなければ作って記録）
    Note over WF,M: Commit state は前のステップが失敗しても実行（!cancelled()）
    WF->>S: last-run を更新
    WF->>M: .state/* に変更があればコミットし、pull --rebase してから push
```

毎日の実行が終わると（成功・失敗にかかわらず）、`pages.yml` がコミットされた最新の状態で CI（リンター・フォーマット・テスト）を確かめ、通ったらサイトを書き出して GitHub Pages に公開する。
毎日の実行のコミットは GITHUB_TOKEN で push するので push では動かず、`workflow_run` で受ける（もののふの登録・引退のあとも同じ）。

## 状態を R2 に置くゲーム

Variable `R2_STATE_BRANCHES` に入れたゲームは、`.state/` の中身を tar.gz にまとめて Cloudflare R2 に置く（#301。設定は [SETUP.md](SETUP.md) の「5. ゲームの状態を R2 に置く」）。

```mermaid
sequenceDiagram
    autonumber
    participant R2 as R2（<ゲーム>/latest.tar.gz）
    participant L as load-state
    participant G as run-game（rpg.py）
    participant S as save-state
    participant P as pages.yml

    L->>R2: 最新と ETag を取得
    L->>G: アーティファクトで渡す（トークンは渡さない）
    G->>G: .state/ を置き換えて進める
    G->>S: 進めた .state/ をアーティファクトで渡す
    S->>R2: snapshots/<日時>-<操作>.tar.gz を書く
    S->>R2: ETag が取得したときと同じなら latest.tar.gz を置き換える
    P->>R2: 最新を取得して、サイトを書き出す
```

- ブランチにはコミットしないので、毎日の実行のあとの公開は、ブランチの先端のコミットと R2 の最新で書き出す
- スナップショットは 30 日で消える（ライフサイクルルール）

## もののふを追加・引退させる workflow

```mermaid
sequenceDiagram
    autonumber
    participant U as 手動実行
    participant RP as rpg.py
    participant S as .state/*
    participant M as master

    U->>RP: new-game.yml（もののふの名前）
    RP->>S: heroes.json に登録、heroes/<id>.json を作る（Gist は作らない）
    S->>M: コミット
    U->>RP: retire-hero.yml（ID か名前）
    RP->>S: heroes.json の active を false に
    S->>M: コミット
```
