# cryepis

GitHub プロフィールに Pin した Gist の中身を、GitHub Actions の定期実行で生成・更新する仕組みです。
ネタ（Pin に載せる内容）ごとにスクリプトと workflow を持ち、今後ネタを追加していく想定です。

## ネタ一覧

| ネタ | Pin する Gist | 更新 | 説明 |
|---|---|---|---|
| [勇者の冒険（テキスト RPG）](#勇者の冒険テキスト-rpg) | ランキング 1 つ | 毎日 JST 7:17 | 勇者たちがギルドの依頼をこなして育っていくのを眺める RPG。3 か月ごとのシーズン制（アイテムだけ持ち越す） |

## 共通の仕組み

- workflow・スクリプト・ファイルの関係と、各ファイルの使い道は [docs/DATA_FLOW.md](docs/DATA_FLOW.md) の図を参照
- `.state/` 配下のファイルの JSON 構成と項目の意味は [docs/STATE_FORMAT.md](docs/STATE_FORMAT.md) を参照
- セットアップ（Gist・PAT・Secrets / Variables）は [docs/SETUP.md](docs/SETUP.md) を参照
  （ネタを追加したら、これらのドキュメントにも追記する）
- Gist の更新には `gist` スコープの Classic PAT（Secret `GIST_PAT`）を使う。Gist の ID は `.state/` のファイル（RPG は `board.json`）に記録する
- Pinned カードには Gist の**名前順で先頭のファイル**の、先頭 5 行（見出し + 4 行）ほどしか出ず、1 行 55 桁前後で切れる。
  各ネタはこの範囲に収まるように出力する（Gist のファイルは 1 つだけにする。1 行は `common.LINE_MAX` = 49 桁まで）
- 記録用のデータは `.state/` に置き、workflow の最後に github-actions[bot] 名義で master に直接コミットする（PR は通さない）
- `.state/last-run` は keepalive 用。毎日コミットし、60 日無活動による scheduled workflow の停止を防ぐ
- 標準ライブラリのみで動く（Python 3.12）。GitHub API・Gist の更新・表示幅の計算は `scripts/common.py` にまとめている

## テスト

```sh
python -m unittest discover -s tests
```

## ネタを追加するとき

1. `scripts/` にスクリプトを、`tests/` にテストを追加する（Gist の更新は `common.update_gist` を使い回せる）
2. ネタごとに `.github/workflows/` に workflow を作る（`hero-adventure.yml` を参考にする）
   - 最初に Test ステップ（`python -m unittest discover -s tests`）を置く
   - `.state/` に書くなら `permissions: contents: write` とコミットのステップを用意する。
     keepalive（`.state/last-run`）はどれか 1 つの毎日の workflow で更新すれば足りる（今は `hero-adventure.yml`）
3. public Gist を作り（ファイルは 1 つ）、ID を管理するしくみ（`.state/` のファイルなど）を用意して Pin する
4. ドキュメントを更新する: この README の「ネタ一覧」とネタごとの節、docs/SETUP.md（Gist・Variable）、
   docs/DATA_FLOW.md（図・ファイルの表）、`.state/` に書く場合は docs/STATE_FORMAT.md（データ形式）

---

## 勇者の冒険（テキスト RPG）

MAP の上で勇者たちが毎日それぞれ動き、町のギルドの依頼をこなしながら育っていく RPG を Gist に表示します。
ゴールはなく、成長を眺める RPG です（設計は [docs/SEASON_DESIGN.md](docs/SEASON_DESIGN.md)。いまは第 1 段階のプロトタイプ）。
勇者は最大 30 人まで同時に冒険できます。見る場所は 2 つあります。

- **ランキングの Gist**（Pin する。Gist はこの 1 つだけ）: 今のシーズンの進み具合と、レベル・討伐数・依頼達成のランキング。
  本文には勇者ごとの短い欄もある。最初の毎日の実行で自動的に作られる
- **勇者ごとの詳細のページ**（`heroes/<id>.md`。リポジトリの中）: その勇者のステータス・装備・持ち物・日誌。毎日のコミットで更新する
- **MAP のページ**（`heroes/MAP.md`）: 世界の MAP を Mermaid の図で表示する。地域ごとにまとめ、勇者が今いる場所に名前を出す。毎日のコミットで更新する

ランキングのカード:

```
🏆 シーズン1  Day 40/90  勇者5人
Lv   🥇カイ 13 🥈ゴール… 13 🥉ボブ 13
討伐 🥇アリス 59 🥇ボブ 59 🥉カイ 57
依頼 🥇CASSIA 16 🥈ゴール… 13 🥉ボブ 12
📰 カイ: 依頼達成！森の町へ荷物を届ける（+72EXP …
```

カードでは名前を表示幅 8（全角 4 文字）までに切り詰めます。ランキングの Gist を開くと、全員の順位（レベル・討伐数・依頼達成・レアアイテム）、
今日の出来事、勇者ごとの欄、歴代のシーズンの 1 位が見られます。勇者ごとの欄はこんな形です。

```
■ アリス  Lv.12  📍 迷いの森 🎯 ウルフ 1/4
  HP 117/123  攻33 防16  1587G  回復薬 3
  装備: 鉄の剣 / 鎖かたびら / 幸運のお守り
  レア: ★狩人の短剣
  今日: ウルフを倒した (+17EXP)、木漏れ日が揺れている
  詳細: https://github.com/<owner>/<repo>/blob/master/heroes/h1.md
```

勇者ごとの詳細のページ（`heroes/<id>.md`）の先頭:

```
⚔️ アリス Lv.12  S1 Day 40
📍 迷いの森 🎯 ウルフ 1/4
HP ██████████ 117/123  攻33 防16  1587G
装備: 鉄の剣 / 鎖かたびら / 幸運のお守り
今日: ウルフを倒した (+17EXP)、木漏れ日が揺れて…
```

続けて、ステータス・装備・持ち物、今シーズンの成績、直近 30 日の冒険の記録、歴代のシーズンの記録が載ります。

### 勇者を増やす・引退させる（手動実行）

| workflow | 入力 | すること |
|---|---|---|
| *New game (add a hero)* | 勇者の名前 | 勇者管理シート `.state/heroes.json` に登録し、状態と詳細のページを作る（Gist は作らない）。冒険は次回の毎日の実行から進む |
| *Retire hero* | 勇者の ID（`h1` など）または名前 | 勇者を引退させる（毎日の更新の対象から外れ、ランキングにも出なくなる。状態と詳細のページは残り、枠が 1 つ空く） |

- 手動実行は 1 件ずつ、終わってから次を実行する（`.state/` への書き込みを直列にするため、待機中に別の実行が起動すると先の待機分が取り消される）
- 現役の勇者は最大 30 人（`MAX_HEROES`）。引退した勇者は数えない。同じ名前の現役の勇者は作れない。名前は表示幅 16（全角 8 文字）まで
- 勇者管理シート `.state/heroes.json` に、勇者の ID・名前・作成日・現役かどうかが入る

### 進み方

- **MAP は同心円の形**: 外側（外縁 Lv1〜8）ほど易しく、第二圏・第三圏・内縁と内側に行くほど敵と町のレベルが上がり、中心（Lv33〜45）がいちばん難しい。外周は 6 つの方角に分かれ、方角ごとにテーマがある（例: 南東は海岸から深海へ）
- **最初の町はランダム**: 外縁の 6 つの町（`starts`）から、勇者ごとに決める。シーズンが変わると決め直す
- 1 日の**行動回数**は乱数で決まる（`world/season.json` の `action_weights`。0 回なら休んで HP が少し回復する）
- 町に着くと HP が全回復し、店で今より良い装備と回復薬を買う。ギルドの掲示板（町ごと・日ごとに全員共通）から、
  レベルに合う依頼を 1 つ受ける。合う依頼がなければ、レベルに合った野外を見回る（報酬なし）
- 依頼は**討伐**（指定の場所で指定の敵を倒す）と**配達**（別の町へ届ける。報告は届け先の町）。報告すると経験値・お金と、
  確率でアイテムがもらえる。期限を過ぎると失敗
- 野外を通ったり探索したりすると、戦闘・宝箱（まれにレア装備）・景色のどれかが起きる
- HP が少なく回復薬もないときは近くの町へ戻る。力尽きたら依頼は失敗になり、最後に寄った町で目を覚ます（お金は減らない）
- 手に入れたアイテムはすべて持ち物に入り、部位（武器・防具・装飾品）ごとにいちばん強いものを自動で装備する
- **シーズン**は 3 か月（`world/season.json` の `start` と `months`）。シーズンが変わると記録を残し、
  **アイテム以外**（レベル・お金・回復薬・位置・依頼）を最初に戻す。アイテムは多少の助けになる程度に抑えている
- 乱数は「日付＋シーズン＋勇者の ID」を種にするので、勇者ごとに展開が違う。同じ日に何度実行しても結果は同じで、その日の分を処理済みなら進めない（Gist の表示だけ更新する）
- workflow が動かなかった日は、次の実行でさかのぼって 1 日ずつ進める（最大 7 日分）
- 1 人の勇者の更新（Gist の API エラーなど）が失敗しても、ほかの勇者は進める（失敗した勇者があれば workflow は失敗で終わる）
- 始めるには Secret `GIST_PAT` を登録して（ランキングの Gist に使う）、*New game* を実行する（[docs/SETUP.md](docs/SETUP.md)）。勇者がいない間、毎日の実行は何もしない

### 世界を広げる（world/ の編集）

| ファイル | 中身 |
|---|---|
| `world/season.json` | シーズンの長さ・初期ステータス・レベルアップ・行動回数の確率・回復薬・宝箱のドロップ表など |
| `world/map.json` | 場所（町・野外）と道、最初の町の候補（`starts`）。場所には地域（`region`。MAP の図の枠になる）、野外にはレベルの範囲・出る敵・出来事の起こりやすさを書く。いまは町 22・野外 47 |
| `world/monsters.json` | 敵 |
| `world/items.json` | アイテムと珍しさ（common / rare / epic / legendary）。店で売るものは `price` を書く |
| `world/quests.json` | ギルドの依頼のひな形（種類・出やすさ・期限・報酬・アイテムのドロップ表） |

- 参照はすべて ID。編集したら `python -m unittest discover -s tests` で確かめる（`tests/test_world.py` がデータの誤りを、
  `tests/test_game.py` の BalanceTest がシーズン末のレベルや依頼の成功率を調べる）
- データに誤りがあると毎日の実行の Test で止まり、その日の冒険は進まない（壊れたデータで進むことはない）
- 場所やアイテムを消しても、勇者の状態は壊れない（消えた場所にいる勇者は最初の町に戻り、消えた依頼は取り消す）
- アイテムが強すぎると検証で止まる（部位ごとの最強装備の合計が、`balance_level` での基本能力値の `item_power_rate` を超えたとき）

| ファイル | 役割 |
|---|---|
| `world/` | 世界のデータ（上の表） |
| `scripts/world.py` | `world/` の読み込みと検証、最短経路の計算 |
| `scripts/game.py` | 1 日分の進行（依頼・戦闘・アイテム・シーズンの切り替え）と、勇者の状態の読み書き |
| `scripts/rpg.py` | 勇者を登録・引退させ、現役の勇者を 1 日分ずつ進めて、詳細のページとランキングの Gist（`hero-ranking.txt`）を更新する |
| `.state/heroes.json` | 勇者管理シート（勇者の一覧） |
| `.state/board.json` | ランキングの Gist の ID と URL |
| `heroes/<id>.md` | 勇者ごとの詳細のページ（rpg.py が書き出す。手で編集しない） |
| `heroes/MAP.md` | MAP のページ（Mermaid の図。rpg.py が書き出す。手で編集しない） |
| `.state/heroes/<id>.json` | 勇者ごとの状態・持ち物・日誌・シーズンの記録（形式は [docs/STATE_FORMAT.md](docs/STATE_FORMAT.md)） |
| `.github/workflows/hero-adventure.yml` | 毎日 JST 7:17 に rpg.py を動かし（ランキングの Gist を更新）、`.state/` と `heroes/` をコミットする |
| `.github/workflows/new-game.yml` / `retire-hero.yml` | 勇者の追加・引退（手動実行） |

```sh
python scripts/rpg.py --dry-run --actions 4       # 記録せずに今日の分を試す（行動回数を指定。勇者が登録済みのとき）
python scripts/rpg.py --new-game 名前              # 勇者を登録する（Actions の New game と同じ）
python scripts/rpg.py --retire 名前またはID        # 勇者を引退させる
python scripts/rpg.py --map                       # MAP のページだけを書き出す（world/map.json を編集したときの確認に）
```
