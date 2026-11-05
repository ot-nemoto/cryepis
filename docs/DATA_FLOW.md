# データの流れ

どの workflow（とスクリプト）が、どのファイルを読み書きし、それが何に使われるかをまとめます。

## 全体図

```mermaid
flowchart LR
    subgraph daily["hero-adventure.yml（毎日 JST 7:17）"]
        direction TB
        test["Test<br/>（unittest）"]
        rpg["rpg.py<br/>テキスト RPG を 1 日進める"]
        commit["Commit state<br/>（master に直接コミット）"]
        test ==> rpg ==> commit
    end

    subgraph state[".state/（master にコミット）"]
        rpgState[("rpg.json<br/>勇者の状態・日誌・周回の記録")]
        lastRun[("last-run<br/>keepalive 用の日付")]
    end

    world[("rpg_world.json<br/>エリア・敵・アイテム")]
    contrib["GitHub GraphQL<br/>前日のコントリビューション数"]
    rpgGist["Pinned Gist<br/>勇者の冒険"]

    world --> rpg
    contrib -->|"進む歩数"| rpg
    rpgState <-->|"前日の状態を読み、今日の分を書く"| rpg
    rpg -->|"冒険の様子"| rpgGist
    commit -->|"日付を更新"| lastRun
```

- 太線（`hero-adventure.yml` の中）: ステップの実行順。各ステップの実行条件は下の「毎日の workflow の処理順」を参照
- 実線: データの読み書き

## ファイルごとの役割

| ファイル | 書き込み元 | 中身 | 使い道 |
|---|---|---|---|
| `rpg_world.json` | 人（手で編集） | テキスト RPG の世界（エリア・敵・ボス・店・イベントの起こりやすさ・歩数の決まり） | `rpg.py` の入力 |
| `.state/rpg.json` | `rpg.py` | 勇者の状態（位置・HP・レベル・装備など）、直近 30 日の日誌、周回の記録 | 翌日の続きと、Gist の表示 |
| `.state/last-run` | Commit state ステップ | 最終実行日（JST） | 60 日無活動で scheduled workflow が止まるのを防ぐ（keepalive） |

`.state/` の各ファイルの JSON 構成・項目の意味・例は [STATE_FORMAT.md](STATE_FORMAT.md) を参照してください。

保持期間:
- `rpg.json` は毎日上書きします（日誌は直近 30 日分、周回の記録は無制限）。
- `last-run` は毎回上書きします。

## 毎日の workflow の処理順

```mermaid
sequenceDiagram
    autonumber
    participant WF as hero-adventure.yml
    participant RP as rpg.py
    participant GH as GitHub API
    participant G as Pinned Gist
    participant S as .state/*
    participant M as master

    WF->>WF: Test（unittest）
    Note over WF,RP: 手動実行で create_gist を指定したときは、rpg.py --create-gist で Gist を作るだけ（冒険は進めない。Commit state は keepalive だけをコミットする）
    Note over WF,RP: Test が失敗したら、または GIST_ID_RPG が未設定なら rpg.py はスキップ
    WF->>RP: 実行
    RP->>GH: 前日（JST）のコントリビューション数（GraphQL）
    RP->>S: rpg.json を 1 日分進める（処理済みなら進めない。実行されなかった日があればさかのぼって進める）
    RP->>G: 勇者の冒険の Gist を更新
    Note over WF,M: Commit state は前のステップが失敗しても実行（!cancelled()）
    WF->>S: last-run を更新
    WF->>M: .state/* に変更があればコミットし、pull --rebase してから push
```
