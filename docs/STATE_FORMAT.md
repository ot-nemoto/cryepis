# .state/ のデータ形式

`.state/` 配下のファイルに、どんなデータがどの JSON 構成で入っているかをまとめます。
どの workflow がいつ書き込むかは [DATA_FLOW.md](DATA_FLOW.md) を参照してください。

| ファイル | 書き込み元 | 中身 |
|---|---|---|
| [`heroes.json`](#heroesjson) | `scripts/rpg.py`（New game / Retire hero） | 勇者管理シート（勇者の一覧と Gist の ID） |
| [`heroes/<id>.json`](#heroesidjson) | `scripts/rpg.py` | 勇者ごとの状態・日誌・周回の記録 |
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
    {"id": "h1", "name": "アリス", "gist_id": "0123abcd…", "gist_url": "https://gist.github.com/…",
     "created": "2026-11-18", "active": true}
  ]
}
```

| 項目 | 中身 |
|---|---|
| `id` | 勇者の ID（`h1`, `h2`, …）。引退しても欠番は再利用しない。状態ファイル名と乱数の種に使う |
| `name` | 勇者の名前（表示幅 16 まで） |
| `gist_id` / `gist_url` | その勇者の Gist |
| `created` | 作った日（JST） |
| `active` | 現役か。`false`（引退）の勇者は毎日の更新の対象外。現役は最大 6 人 |

---

## `heroes/<id>.json`

勇者ごとの状態です（テキスト RPG は [README](../README.md#勇者の冒険テキスト-rpg)）。`scripts/rpg.py` が毎日 1 日分進めて上書きします。
世界の設定（エリア・敵・アイテム）は `rpg_world.json` にあり、ここには勇者の状態だけを持ちます。

```json
{
  "id": "h1",
  "name": "アリス",
  "day": 37,
  "lap": 1,
  "lap_day": 37,
  "pos": 26,
  "boss_cleared": ["草原"],
  "hero": {"level": 7, "exp": 120, "max_hp": 90, "hp": 74, "base_atk": 23, "base_def": 8,
           "gold": 340, "potions": 2, "weapon": {"name": "鉄の剣", "power": 7}, "armor": null},
  "kills": 41,
  "deaths": 0,
  "last_date": "2026-11-11",
  "last": {"date": "2026-11-11", "steps": 2, "events": ["ウルフを倒した！ (+10EXP)", "…"]},
  "journal": [{"date": "2026-11-11", "steps": 2, "text": "ウルフを倒した！ (+10EXP) / …"}],
  "records": [{"lap": 1, "days": 92, "deaths": 1, "level": 12, "cleared_on": "2027-01-05"}]
}
```

| 項目 | 中身 |
|---|---|
| `id` / `name` | 勇者の ID と名前（`heroes.json` と同じ） |
| `day` | 通算の日数（周回をまたいで数える） |
| `lap` / `lap_day` | 何周目か / その周の何日目か |
| `pos` | 今いるマス（0 から。エリアの最初のマスが町、最後のマスがボス） |
| `boss_cleared` | この周で倒したボスのエリア名 |
| `hero` | レベル、経験値（次のレベルまでの途中の値）、HP、基本の攻撃力・防御力、所持金、回復薬、装備 |
| `kills` / `deaths` | この周で倒した敵の数 / 力尽きた回数 |
| `last_date` | 最後に進めた日（JST）。この日以前の日付では進めない（同じ日に再実行しても進まない） |
| `last` | 最後に進めた日の結果（`date` は進めた日、`steps` は進んだ歩数（乱数で決まる）、`events` は出来事） |
| `journal` | 日誌。新しい順に直近 30 日分（`JOURNAL_DAYS`） |
| `records` | クリアした周の記録（何日でクリアしたか、力尽きた回数、レベル）。無制限に残す |

- 魔王を倒すと `records` に記録を足し、`lap` を 1 増やして、`pos`・`hero`・`kills`・`deaths` を最初の状態に戻します。
- workflow が動かなかった日は、次の実行で `last_date` の翌日から 1 日ずつ進めます（最大 7 日分。`CATCH_UP_DAYS`）。
- 読み込むときに、足りない項目は初期値で補い、`rpg_world.json` を縮めて世界の外に出た `pos` は最後のマスに戻します。

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
print(f"{state['lap']}周目 {state['lap_day']}日目 Lv.{hero['level']} HP {hero['hp']}/{hero['max_hp']}")
for r in state["records"]:
    print(f"{r['lap']}周目: {r['days']}日でクリア")
```
