# cryepis

GitHub プロフィールに Pin した Gist の中身を、GitHub Actions の定期実行で生成・更新する仕組みです。
ネタ（Pin に載せる内容）ごとにスクリプトと workflow を持ち、今後ネタを追加していく想定です。

## ネタ一覧

| ネタ | Pin する Gist | 更新 | 説明 |
|---|---|---|---|
| [勇者の冒険（テキスト RPG）](#勇者の冒険テキスト-rpg) | 1 つ | 毎日 JST 7:17 | 前日のコントリビューション数だけ勇者が進む RPG。魔王を倒すとクリアして、もう一度最初から |

## 共通の仕組み

- workflow・スクリプト・ファイルの関係と、各ファイルの使い道は [docs/DATA_FLOW.md](docs/DATA_FLOW.md) の図を参照
- `.state/` 配下のファイルの JSON 構成と項目の意味は [docs/STATE_FORMAT.md](docs/STATE_FORMAT.md) を参照
- セットアップ（Gist・PAT・Secrets / Variables）は [docs/SETUP.md](docs/SETUP.md) を参照
  （ネタを追加したら、これらのドキュメントにも追記する）
- Gist の更新には `gist` スコープの Classic PAT（Secret `GIST_PAT`）を使う。Gist ごとの ID は Repository variable で渡す
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
3. public Gist を作り（ファイルは 1 つ）、ID を Repository variable に登録して Pin する
4. ドキュメントを更新する: この README の「ネタ一覧」とネタごとの節、docs/SETUP.md（Gist・Variable）、
   docs/DATA_FLOW.md（図・ファイルの表）、`.state/` に書く場合は docs/STATE_FORMAT.md（データ形式）

---

## 勇者の冒険（テキスト RPG）

勇者が毎日少しずつ冒険を進める RPG を Pinned Gist に表示します。**前日（JST）の自分のコントリビューション数**
（コミット・PR・Issue・レビュー）で、その日に進む歩数が決まります。コミットするほど勇者が進みます。

```
⚔️ Day 37  勇者 Lv.5 ── 🦇 地下洞窟
🏠━━━━━━━━━━━🧙────────────🏰 魔王城まで59歩
HP ██████░░░░ 41/70  攻24 防15  178G
昨日(0 contrib): 夢の中で冒険の続きを見た
今日: 冷たい風が奥から吹いてくる…
```

Gist を開くと、ステータス・装備、直近 30 日の冒険の記録、歴代のクリアの記録が見られます。

| 前日のコントリビューション数 | 進む歩数 |
|---|---|
| 0 | 0 歩（休息日。HP が少し回復する） |
| 1〜2 | 1 歩 |
| 3〜5 | 2 歩 |
| 6 以上 | 3 歩 |

- 草原 → 迷いの森 → 地下洞窟 → 雪山 → 魔王城と進む。各エリアの最初のマスは町（回復・装備の購入）、最後のマスはボス
- 1 歩ごとに、戦闘・宝箱・旅の商人・景色・まれなイベント（能力が上がる、伝説の剣など）のどれかが起きる
- HP が 0 になったら、そのエリアの町に戻され、所持金が半分になる（レベルと装備はそのまま。町で回復し、残ったお金で買い物をする）
- 魔王を倒すとクリア。記録を残し、レベルと装備をリセットして最初からもう一度挑む（周回ごとにクリアまでの日数を比べられる）
- 平均的なペース（コントリビューションが 0 の日が 3 割程度）で、1 周 90 日前後になるように調整している（`tests/test_rpg.py` でまとめて試している）
- 乱数は「日付＋周回数」を種にするので、同じ日に何度実行しても結果は同じ。その日の分を処理済みなら進めない（Gist の表示だけ更新する）
- workflow が動かなかった日や、コントリビューション数を取得できなかった日（警告を出す）は、次の実行でさかのぼって 1 日ずつ進める（最大 7 日分）
- Repository variable `GIST_ID_RPG` を登録するまでは動かない（RPG を使わないなら何もしなくてよい）。
  始めるための Gist は、workflow の手動実行で `create_gist` を指定すると作れる（[docs/SETUP.md](docs/SETUP.md)）

| ファイル | 役割 |
|---|---|
| `rpg_world.json` | 世界の設定（エリア・敵・ボス・店・イベントの起こりやすさ・歩数の決まり）。敵やエリアを足すときはここを編集する |
| `scripts/rpg.py` | 1 日分を進めて、Gist（`hero-adventure.txt`）を更新する |
| `.state/rpg.json` | 勇者の状態・日誌・周回の記録（形式は [docs/STATE_FORMAT.md](docs/STATE_FORMAT.md)） |
| `.github/workflows/hero-adventure.yml` | 毎日 JST 7:17 に rpg.py を動かし、`.state/rpg.json` をコミットする |

```sh
python scripts/rpg.py --dry-run --contributions 3   # 記録せずに今日の分を試す（前日のコントリビューション数を指定）
GIST_PAT=xxx python scripts/rpg.py --create-gist    # 冒険を始めるための Gist を作る（Actions の手動実行の create_gist でも作れる）
```

`rpg_world.json` を変えたら、`tests/test_rpg.py` のバランスのテスト（1 周 70〜120 日に収まるか）が通るか確かめてください。
