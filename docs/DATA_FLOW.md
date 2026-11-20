# データの流れ

どの workflow（とスクリプト）が、どのファイルを読み書きし、それが何に使われるかをまとめます。

## 全体図

```mermaid
flowchart LR
    subgraph daily["hero-adventure.yml（毎日 JST 7:17）"]
        direction TB
        test["Test<br/>（unittest）"]
        rpg["rpg.py（world.py・game.py）<br/>現役の勇者を全員 1 日進める"]
        commit["Commit state<br/>（master に直接コミット）"]
        test ==> rpg ==> commit
    end

    subgraph manual["手動実行"]
        newgame["new-game.yml<br/>勇者を追加"]
        retire["retire-hero.yml<br/>勇者を引退"]
    end

    subgraph state[".state/（master にコミット）"]
        heroes[("heroes.json<br/>勇者管理シート")]
        board[("board.json<br/>ランキングの Gist の ID")]
        heroState[("heroes/&lt;id&gt;.json<br/>勇者ごとの状態・持ち物・日誌・シーズンの記録")]
        lastRun[("last-run<br/>keepalive 用の日付")]
    end

    world[("world/*.json<br/>シーズン・MAP・敵・アイテム・依頼")]
    pages[("heroes/&lt;id&gt;.md<br/>勇者ごとの詳細のページ")]
    ranking["ランキングの Gist<br/>（Pin する）"]

    world --> rpg
    heroes -->|"現役の勇者と Gist の ID"| rpg
    heroState <-->|"前日の状態を読み、今日の分を書く"| rpg
    rpg -->|"冒険の様子"| pages
    rpg -->|"シーズンの順位"| ranking
    board <-->|"Gist の ID（なければ作って記録）"| rpg
    newgame -->|"勇者を登録"| heroes
    newgame -->|"初期状態"| heroState
    newgame -->|"冒険前のページ"| pages
    retire -->|"active を false に"| heroes
    commit -->|"日付を更新"| lastRun
```

- 太線（`hero-adventure.yml` の中）: ステップの実行順。各ステップの実行条件は下の「毎日の workflow の処理順」を参照
- 実線: データの読み書き
- 手動実行の workflow は毎日の workflow と同じ `concurrency` グループで、`.state/` への書き込みが重ならないようにしている

## ファイルごとの役割

| ファイル | 書き込み元 | 中身 | 使い道 |
|---|---|---|---|
| `world/*.json` | 人（手で編集） | テキスト RPG の世界（シーズン・MAP・敵・アイテム・ギルドの依頼）。形式は README の「世界を広げる」 | `rpg.py` の入力（Test で検証してから使う） |
| `.state/heroes.json` | `rpg.py`（New game / Retire hero） | 勇者の一覧（ID・名前・Gist の ID と URL・作成日・現役かどうか）。現役は最大 30 人 | 毎日の更新の対象と、更新先の Gist |
| `heroes/MAP.md` / `MAP.svg` | `rpg.py` | 世界の地図の画像（SVG）と Mermaid の図、勇者の現在地 | GitHub 上で見る。ランキングの Gist からリンクする |
| `heroes/<id>.md` | `rpg.py` | 勇者ごとの詳細（ステータス・装備・持ち物・日誌・シーズンの記録）を読むためのページ | GitHub 上で読む。ランキングの Gist からリンクする |
| `.state/heroes/<id>.json` | `rpg.py` | 勇者の状態（場所・HP・レベル・依頼など）、持ち物、直近 30 日の日誌、シーズンの記録 | 翌日の続きと、Gist の表示 |
| `.state/board.json` | `rpg.py`（最初の毎日の実行） | ランキングの Gist の ID と URL | 毎日更新するランキングの Gist |
| `.state/last-run` | Commit state ステップ | 最終実行日（JST） | 60 日無活動で scheduled workflow が止まるのを防ぐ（keepalive） |

`.state/` の各ファイルの JSON 構成・項目の意味・例は [STATE_FORMAT.md](STATE_FORMAT.md) を参照してください。

保持期間:
- `heroes/<id>.json` は毎日上書きします（日誌は直近 30 日分、シーズンの記録と持ち物は無制限）。引退した勇者のファイルはそのまま残ります。
- `heroes.json` は勇者の追加・引退のときだけ書き換えます。
- `board.json` はランキングの Gist を作ったときに 1 回だけ書きます。
- `last-run` は毎回上書きします。

## 毎日の workflow の処理順

```mermaid
sequenceDiagram
    autonumber
    participant WF as hero-adventure.yml
    participant RP as rpg.py
    participant G as ランキングの Gist
    participant S as .state/*
    participant M as master

    WF->>WF: Test（unittest）
    Note over WF,RP: Test が失敗したら rpg.py はスキップ。勇者がいなければ何もしない
    WF->>RP: 実行
    RP->>S: heroes.json から現役の勇者を読む
    loop 現役の勇者ごと（失敗は勇者ごとに隔離）
        RP->>S: heroes/<id>.json を 1 日分進める（行動回数は乱数。シーズンが変わったら持ち物以外をリセット。処理済みなら進めない。実行されなかった日があればさかのぼって進める）
        RP->>S: heroes/<id>.md（詳細のページ）を書き出す
    end
    RP->>S: heroes/MAP.md と MAP.svg（地図）を書き出す
    RP->>G: ランキングの Gist を更新（board.json がなければ作って記録）
    Note over WF,M: Commit state は前のステップが失敗しても実行（!cancelled()）
    WF->>S: last-run を更新
    WF->>M: .state/* に変更があればコミットし、pull --rebase してから push
```

## 勇者を追加・引退させる workflow

```mermaid
sequenceDiagram
    autonumber
    participant U as 手動実行
    participant RP as rpg.py
    participant S as .state/*
    participant M as master

    U->>RP: new-game.yml（勇者の名前）
    RP->>S: heroes.json に登録、heroes/<id>.json と heroes/<id>.md を作る（Gist は作らない）
    S->>M: コミット
    U->>RP: retire-hero.yml（ID か名前）
    RP->>S: heroes.json の active を false に
    S->>M: コミット
```
