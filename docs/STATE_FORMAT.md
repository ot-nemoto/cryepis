# .state/ のデータ形式

`.state/` 配下のファイルに、どんなデータがどの JSON 構成で入っているかをまとめます。
どの workflow がいつ書き込むかは [DATA_FLOW.md](DATA_FLOW.md) を参照してください。

| ファイル | 書き込み元 | 中身 |
|---|---|---|
| [`heroes.json`](#heroesjson) | `scripts/rpg.py`（New game / Retire hero） | 勇者管理シート（勇者の一覧） |
| [`board.json`](#boardjson) | `scripts/rpg.py`（最初の毎日の実行） | ランキングの Gist の ID と URL |
| [`guild.json`](#guildjson) | `scripts/rpg.py`（毎日の実行） | 町ごとのギルドの掲示板（日をまたいで残る。早い者勝ち） |
| [`heroes/<id>.json`](#heroesidjson) | `scripts/rpg.py` | 勇者ごとの状態・持ち物・日誌・シーズンの記録 |
| [`last-run`](#last-run) | hero-adventure.yml の Commit state ステップ | 最終実行日 |

共通の決まり:

- 日付はすべて JST の `YYYY-MM-DD`（文字列）
- インデント（2 スペース）付きの整形済み JSON（UTF-8）で保存する
- workflow の最後に github-actions[bot] が master に直接コミットする（変更が無ければコミットしない）

---

## heroes.json

勇者管理シートです。*New game* が勇者を追加し、*Retire hero* が `active` を `false` にします。

```json
{
  "heroes": [
    {"id": "h1", "name": "アリス", "created": "2026-11-18", "active": true}
  ]
}
```

| 項目 | 中身 |
|---|---|
| `id` | 勇者の ID（`h1`, `h2`, …）。引退しても欠番は再利用しない。状態ファイル名と乱数の種に使う |
| `name` | 勇者の名前（表示幅 32 まで。全角 16 文字・半角 32 文字） |
| `created` | 作った日（JST） |
| `active` | 現役か。`false`（引退）の勇者は毎日の更新の対象外。現役は最大 30 人 |

---

## board.json

ランキングの Gist（Pin する Gist）の記録です。毎日の実行で、このファイルがなければ Gist を作って書き込みます（以降は書き換えません）。
ファイルを消すと、次の実行で新しいランキングの Gist が作られます（古い Gist は残ります）。

```json
{"gist_id": "0123abcd…", "gist_url": "https://gist.github.com/…", "created": "2026-12-01"}
```

---

## guild.json

町ごとのギルドの掲示板です。全員で共有し、日をまたいで残ります（早い者勝ち）。毎日の実行で書き換えます（依頼 1 件を 1 行で書きます）。

| 項目 | 内容 |
|---|---|
| `season` | 掲示板のシーズン。シーズンが変わったら全部貼り直す |
| `refreshed` | 町の ID → その町の掲示板を朝の状態に貼り替えた日（町ごとに 1 日 1 回） |
| `towns` | 町の ID → 貼られている依頼の一覧。依頼の項目は勇者の `quest` と同じで、下の 4 つが増える |

| 依頼に増える項目 | 内容 |
|---|---|
| `posted` | 貼られた日（失敗して戻ったときは戻った日）。この日から `quest_open_days` 日、受け付ける |
| `status` | `open`（受付中）/ `held`（受注中）/ `done`（達成。翌朝はがして新しい依頼を貼る）/ `failed`（失敗。翌朝、受付中に戻す） |
| `holder` | 受けた勇者の ID（受付中は null） |
| `returned` | 前の勇者が失敗して、受付中に戻った依頼なら true |

依頼の ID は「貼られた日付-町の ID-番号」です。受けた勇者がその依頼を持っていない（引退したなど）受注中の依頼は、毎日の実行の最後に失敗にします（翌朝、受付中に戻ります）。
前の形式（`{"taken": {...}}`）や、消した場合は、新しく貼り直します（受注中の勇者は依頼を続けられますが、掲示板には出なくなります）。

```json
{
 "season": 1,
 "refreshed": {"aurea":"2026-12-03"},
 "towns": {
 "aurea": [
  {"type":"deliver","place":"astol","report":"astol",…,"id":"2026-12-01-aurea-1","posted":"2026-12-01","status":"held","holder":"h2","returned":false},
  {"type":"hunt","place":"flumen","report":"aurea",…,"id":"2026-12-03-aurea-0","posted":"2026-12-03","status":"open","holder":null,"returned":false}
 ]
 }
}
```

---

## `heroes/<id>.json`

勇者ごとの状態です（テキスト RPG は [README](../README.md#勇者の冒険テキスト-rpg)）。`scripts/rpg.py` が毎日 1 日分進めて上書きします。
世界の設定（MAP・敵・アイテム・依頼）は `world/` にあり、ここには勇者の状態だけを持ちます。場所・敵・アイテムは `world/` の ID で参照します。

```json
{
  "id": "h1",
  "name": "アリス",
  "season": 2,
  "season_day": 40,
  "day": 130,
  "last_date": "2027-04-09",
  "trait": "swift",
  "personality": "careful",
  "hero": {"level": 12, "exp": 120, "max_hp": 118, "hp": 117, "base_atk": 24, "base_def": 15, "base_spd": 25, "base_dex": 20,
           "gold": 1587, "potions": 3, "stored": 2},
  "inventory": [{"id": "steel_sword", "skill": "pierce"}, {"id": "iron_sword", "skill": null}, {"id": "iron_shield", "skill": null}],
  "storage": [{"id": "wooden_helm", "skill": null}],
  "place": "salina",
  "home_town": "salina",
  "road": null,
  "quest": {"type": "hunt", "name": "討伐", "template": "hunt", "town": "salina", "place": "eques",
            "report": "salina", "monster": "wild_dog", "count": 4, "progress": 1, "level": [1, 1],
            "deadline": null, "reward": {"exp": 112, "gold": 140, "item": [["none", 85], ["common", 10], ["rare", 5]]}},
  "season_stats": {"quests_done": 12, "quests_failed": 1, "kills": 61, "deaths": 0, "rares": []},
  "last": {"date": "2027-04-09", "actions": 3, "events": ["野犬を倒した (+17EXP)", "…"]},
  "journal": [{"date": "2027-04-09", "actions": 3, "text": "野犬を倒した (+17EXP) / …"}],
  "seasons": [{"season": 1, "days": 90, "level": 21, "quests_done": 30, "quests_failed": 2, "kills": 144,
               "deaths": 1, "rares": ["blue_flame_sword"], "ended_on": "2027-02-28"}]
}
```

| 項目 | 中身 |
|---|---|
| `id` / `name` | 勇者の ID と名前（`heroes.json` と同じ） |
| `season` / `season_day` | 今のシーズンの番号（まだ一度も進めていなければ `null`）/ そのシーズンで進めた日数 |
| `day` | 通算の日数（シーズンをまたいで数える） |
| `last_date` | 最後に進めた日（JST）。この日以前の日付では進めない（同じ日に再実行しても進まない） |
| `hero` | レベル、経験値（次のレベルまでの途中の値）、HP、基本の最大 HP・攻撃力・防御力（装備なし）、所持金、回復薬、溜めた行動力（`stored`。シーズンが変わると 0） |
| `inventory` | 持っている装備（1 つずつ `{"id": アイテム, "skill": スキルか null}`）。部位ごとに `season.json` の `carry` の数まで。**シーズンをまたいで持ち越す**。ふだん着ける装備は、持ち物から部位ごとにいちばん強いものを毎回選ぶ（状態には持たない）。前の形式（ID → 個数）も読める |
| `storage` | 保管庫の装備（形は `inventory` と同じ）。持てる数を超えた分を、町のギルドで預ける。シーズンをまたいで持ち越す |
| `place` | 今いる町の ID（道の途中なら出発した町） |
| `home_town` | 最後に寄った町（力尽きたらここで目を覚ます） |
| `trait` | 特性の ID（season.json の `hero_traits`）。勇者を作るときにランダムで決まり、シーズンが変わっても変わらない |
| `stay` | 今の町に着いた日 `{"town": 町, "since": 日付}`（最後に別の町に着いた日。討伐で同じ町に戻っても変わらない）。着いた日から `stay_days_max` 日たったら（着いた日を 0 日目として数える）旅に出る。シーズンが変わると `null` |
| `personality` | 性格の ID（season.json の `personalities`）。勇者を作るときにランダムで決まり、シーズンが変わっても変わらない。ない・消えた性格は、読み込むときに付け直す |
| `road` | 道を進んでいる途中なら `{"to": 行き先の町, "left": 残りの行動回数}`、町にいれば `null` |
| `quest` | 受けている依頼（なければ `null`）。`type` は `hunt`（討伐）/ `deliver`（配達）/ `patrol`（となりの町への旅。報酬なし）。`place` が目的地（討伐は依頼の道の向こうの町）、`report` が報告する町（旅は `null`）、`progress` / `count` が進み具合、`cost` が必要な行動力、`steps` が討伐の残りの段取り（`[["go"/"hunt"/"back", 残りの行動力], ...]`）、`away` が討伐で町から何行動ぶん離れているか、`deadline` は使わない（`null`。期限はない）、`level` が依頼の Lv（討伐は狙う魔物の Lv。配達は Lv を持たず `null`。旅は持たない）、`rank` がレアリティ（1〜4。★ の数）、`name` が依頼の名前、`text` がギルドの言葉（町の依頼だけ） |
| `season_stats` | 今のシーズンの成績（達成・失敗した依頼の数、倒した敵の数、力尽きた回数、手に入れたレアアイテムの ID） |
| `last` | 最後に進めた日の結果（`date` は進めた日、`actions` はその日の行動回数（season.json の actions_per_day）、`events` は出来事） |
| `journal` | 日誌。新しい順に直近 30 日分（`JOURNAL_DAYS`） |
| `seasons` | 終わったシーズンの記録（日数、最後のレベル、成績、終わった日）。無制限に残す |

- シーズンが変わった最初の実行で、`seasons` に記録を足し、`inventory`・`storage` 以外（`hero`・`place`・`quest`・`season_stats` など）を最初の状態に戻します。
- workflow が動かなかった日は、次の実行で `last_date` の翌日から 1 日ずつ進めます（最大 7 日分。`CATCH_UP_DAYS`）。
- 読み込むときに、足りない項目は初期値で補います。`world/` から消えた場所にいれば最初の町に戻し、消えたアイテムは持ち物から外し、
  消えた場所・町・敵を参照する依頼は取り消します。
- 前の形式（一本道の RPG。`season` がない）の状態は、読み込むときに新しい形式で最初から作り直します（ID と名前だけ引き継ぐ）。

---

## last-run

最終実行日（JST）が 1 行だけ入ったテキストファイルです（JSON ではありません）。

```
2026-10-01
```

毎日の workflow で上書きします。60 日間コミットが無いと scheduled workflow が止まるのを防ぐための keepalive 用で、
データとしての使い道はありません。

---

## 読み方の例

```python
import json

with open(".state/heroes/h1.json", encoding="utf-8") as f:
  state = json.load(f)
hero = state["hero"]
print(f"シーズン{state['season']} {state['season_day']}日目 Lv.{hero['level']} 所持金 {hero['gold']}G")
print("持ち物:", ", ".join(x["id"] + (f"〔{x['skill']}〕" if x["skill"] else "") for x in state["inventory"]))
for r in state["seasons"]:
  print(f"シーズン{r['season']}: Lv.{r['level']} 依頼達成 {r['quests_done']}")
```
