# cryepis

GitHub プロフィールに Pin した Gist の中身を、GitHub Actions の定期実行で生成・更新する仕組みです。
ネタ（Pin に載せる内容）ごとにスクリプトと workflow を持ち、今後ネタを追加していく想定です。

## ネタ一覧

| ネタ | Pin する Gist | 更新 | 説明 |
|---|---|---|---|
| [勇者の冒険（テキスト RPG）](#勇者の冒険テキスト-rpg) | 勇者ごとに 1 つ（最大 6 人） | 毎日 JST 7:17 | 毎日ランダムに進む RPG。勇者は手動実行で増やす。魔王を倒すとクリアして、もう一度最初から |

## 共通の仕組み

- workflow・スクリプト・ファイルの関係と、各ファイルの使い道は [docs/DATA_FLOW.md](docs/DATA_FLOW.md) の図を参照
- `.state/` 配下のファイルの JSON 構成と項目の意味は [docs/STATE_FORMAT.md](docs/STATE_FORMAT.md) を参照
- セットアップ（Gist・PAT・Secrets / Variables）は [docs/SETUP.md](docs/SETUP.md) を参照
  （ネタを追加したら、これらのドキュメントにも追記する）
- Gist の更新には `gist` スコープの Classic PAT（Secret `GIST_PAT`）を使う。Gist の ID は `.state/` のファイル（RPG は `heroes.json`）に記録する
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

勇者が毎日少しずつ冒険を進める RPG を Gist に表示します。**1 日に進む歩数は乱数で決まります**（GitHub の活動には依存しません）。
勇者は最大 6 人まで同時に冒険でき、勇者ごとに Gist を持ちます。**Pin できるのは 6 件まで**（repo と合わせて）なので、
好きな勇者の Gist を選んで Pin してください。

```
⚔️ Day 37  アリス Lv.5 ── 🦇 地下洞窟
🏠━━━━━━━━━━━🧙────────────🏰 魔王城まで59歩
HP ██████░░░░ 41/70  攻24 防15  178G
昨日(0歩): 夢の中で冒険の続きを見た
今日: 冷たい風が奥から吹いてくる…
```

Gist を開くと、ステータス・装備、直近 30 日の冒険の記録、歴代のクリアの記録が見られます。

### 勇者を増やす・引退させる（手動実行）

| workflow | 入力 | すること |
|---|---|---|
| *New game (add a hero)* | 勇者の名前 | 新しい public Gist を作り、勇者管理シート `.state/heroes.json` に登録する。冒険は次回の毎日の実行から進む |
| *Retire hero* | 勇者の ID（`h1` など）または名前 | 勇者を引退させる（毎日の更新の対象から外れる。Gist と状態は残り、枠が 1 つ空く） |

- 手動実行は 1 件ずつ、終わってから次を実行する（`.state/` への書き込みを直列にするため、待機中に別の実行が起動すると先の待機分が取り消される）
- 現役の勇者は最大 6 人（`MAX_HEROES`）。同じ名前の現役の勇者は作れない。名前は表示幅 16（全角 8 文字）まで
- 勇者管理シート `.state/heroes.json` に、勇者の ID・名前・Gist の ID と URL・作成日・現役かどうかが入る

### 進み方

| 1 日に進む歩数 | 確率 |
|---|---|
| 0 歩（休息日。HP が少し回復する） | 30% |
| 1 歩 | 30% |
| 2 歩 | 25% |
| 3 歩 | 15% |

- 草原 → 迷いの森 → 地下洞窟 → 雪山 → 魔王城と進む。各エリアの最初のマスは町（回復・装備の購入）、最後のマスはボス
- 1 歩ごとに、戦闘・宝箱・旅の商人・景色・まれなイベント（能力が上がる、伝説の剣など）のどれかが起きる
- HP が 0 になったら、そのエリアの町に戻され、所持金が半分になる（レベルと装備はそのまま。町で回復し、残ったお金で買い物をする）
- 魔王を倒すとクリア。記録を残し、レベルと装備をリセットして最初からもう一度挑む（周回ごとにクリアまでの日数を比べられる）
- 1 周は 90 日前後になるように調整している（`tests/test_rpg.py` でまとめて試している）
- 乱数は「日付＋周回数＋勇者の ID」を種にするので、勇者ごとに展開が違う。同じ日に何度実行しても結果は同じで、その日の分を処理済みなら進めない（Gist の表示だけ更新する）
- workflow が動かなかった日は、次の実行でさかのぼって 1 日ずつ進める（最大 7 日分）
- 1 人の勇者の更新（Gist の API エラーなど）が失敗しても、ほかの勇者は進める（失敗した勇者があれば workflow は失敗で終わる）
- 始めるには Secret `GIST_PAT` を登録して、*New game* を実行する（[docs/SETUP.md](docs/SETUP.md)）。勇者がいない間、毎日の実行は何もしない

| ファイル | 役割 |
|---|---|
| `rpg_world.json` | 世界の設定（エリア・敵・ボス・店・イベントの起こりやすさ・歩数の確率）。敵やエリアを足すときはここを編集する |
| `scripts/rpg.py` | 勇者を登録・引退させ、現役の勇者を 1 日分ずつ進めて Gist（`hero-adventure.txt`）を更新する |
| `.state/heroes.json` | 勇者管理シート（勇者の一覧） |
| `.state/heroes/<id>.json` | 勇者ごとの状態・日誌・周回の記録（形式は [docs/STATE_FORMAT.md](docs/STATE_FORMAT.md)） |
| `.github/workflows/hero-adventure.yml` | 毎日 JST 7:17 に rpg.py を動かし、`.state/` をコミットする |
| `.github/workflows/new-game.yml` / `retire-hero.yml` | 勇者の追加・引退（手動実行） |

```sh
python scripts/rpg.py --dry-run --steps 3         # 記録せずに今日の分を試す（歩数を指定。勇者が登録済みのとき）
GIST_PAT=xxx python scripts/rpg.py --new-game 名前  # 勇者を登録して Gist を作る（Actions の New game と同じ）
python scripts/rpg.py --retire 名前またはID        # 勇者を引退させる
```
