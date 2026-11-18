# データの流れ

どの workflow（とスクリプト）が、どのファイルを読み書きし、それが何に使われるかをまとめます。

## 全体図

```mermaid
flowchart LR
    subgraph daily["hero-adventure.yml（毎日 JST 7:17）"]
        direction TB
        test["Test<br/>（unittest）"]
        rpg["rpg.py<br/>現役の勇者を全員 1 日進める"]
        commit["Commit state<br/>（master に直接コミット）"]
        test ==> rpg ==> commit
    end

    subgraph manual["手動実行"]
        newgame["new-game.yml<br/>勇者を追加"]
        retire["retire-hero.yml<br/>勇者を引退"]
    end

    subgraph state[".state/（master にコミット）"]
        heroes[("heroes.json<br/>勇者管理シート")]
        heroState[("heroes/&lt;id&gt;.json<br/>勇者ごとの状態・日誌・周回の記録")]
        lastRun[("last-run<br/>keepalive 用の日付")]
    end

    world[("rpg_world.json<br/>エリア・敵・アイテム・歩数の確率")]
    gists["勇者ごとの Gist<br/>（Pin は好きなものを最大 6 件）"]

    world --> rpg
    heroes -->|"現役の勇者と Gist の ID"| rpg
    heroState <-->|"前日の状態を読み、今日の分を書く"| rpg
    rpg -->|"冒険の様子"| gists
    newgame -->|"Gist を作り、勇者を登録"| heroes
    newgame -->|"初期状態"| heroState
    newgame -->|"冒険前のカード"| gists
    retire -->|"active を false に"| heroes
    commit -->|"日付を更新"| lastRun
```

- 太線（`hero-adventure.yml` の中）: ステップの実行順。各ステップの実行条件は下の「毎日の workflow の処理順」を参照
- 実線: データの読み書き
- 手動実行の workflow は毎日の workflow と同じ `concurrency` グループで、`.state/` への書き込みが重ならないようにしている

## ファイルごとの役割

| ファイル | 書き込み元 | 中身 | 使い道 |
|---|---|---|---|
| `rpg_world.json` | 人（手で編集） | テキスト RPG の世界（エリア・敵・ボス・店・イベントの起こりやすさ・歩数の確率） | `rpg.py` の入力 |
| `.state/heroes.json` | `rpg.py`（New game / Retire hero） | 勇者の一覧（ID・名前・Gist の ID と URL・作成日・現役かどうか）。最大 6 人 | 毎日の更新の対象と、更新先の Gist |
| `.state/heroes/<id>.json` | `rpg.py` | 勇者の状態（位置・HP・レベル・装備など）、直近 30 日の日誌、周回の記録 | 翌日の続きと、Gist の表示 |
| `.state/last-run` | Commit state ステップ | 最終実行日（JST） | 60 日無活動で scheduled workflow が止まるのを防ぐ（keepalive） |

`.state/` の各ファイルの JSON 構成・項目の意味・例は [STATE_FORMAT.md](STATE_FORMAT.md) を参照してください。

保持期間:
- `heroes/<id>.json` は毎日上書きします（日誌は直近 30 日分、周回の記録は無制限）。引退した勇者のファイルはそのまま残ります。
- `heroes.json` は勇者の追加・引退のときだけ書き換えます。
- `last-run` は毎回上書きします。

## 毎日の workflow の処理順

```mermaid
sequenceDiagram
    autonumber
    participant WF as hero-adventure.yml
    participant RP as rpg.py
    participant G as 勇者ごとの Gist
    participant S as .state/*
    participant M as master

    WF->>WF: Test（unittest）
    Note over WF,RP: Test が失敗したら rpg.py はスキップ。勇者がいなければ何もしない
    WF->>RP: 実行
    RP->>S: heroes.json から現役の勇者を読む
    loop 現役の勇者ごと（失敗は勇者ごとに隔離）
        RP->>S: heroes/<id>.json を 1 日分進める（歩数は乱数。処理済みなら進めない。実行されなかった日があればさかのぼって進める）
        RP->>G: その勇者の Gist を更新
    end
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
    participant G as Gist
    participant S as .state/*
    participant M as master

    U->>RP: new-game.yml（勇者の名前）
    RP->>G: 冒険前のカードで新しい Gist を作る
    RP->>S: heroes.json に登録、heroes/<id>.json を作る
    S->>M: コミット
    U->>RP: retire-hero.yml（ID か名前）
    RP->>S: heroes.json の active を false に
    S->>M: コミット
```
