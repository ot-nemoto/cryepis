#!/usr/bin/env python3
"""コミット数で勇者が進むテキスト RPG を 1 日分進め、Pinned 用の Gist に書き込む。

前日（JST）の自分の GitHub のコントリビューション数（コミット・PR・Issue・レビュー）で、その日に進む歩数が決まる
（rpg_world.json の step_rule。0 なら休息日）。1 歩ごとに戦闘・宝箱・商人などのイベントが起き、
エリアの最後のボスを倒すと次のエリアへ進む。魔王を倒すとクリアで、記録を残して最初からもう一度挑戦する。
HP が 0 になったら、そのエリアの町に戻され、所持金が半分になる。
workflow が動かなかった日やコントリビューション数を取れなかった日があれば、次の実行で
最大 CATCH_UP_DAYS 日分までさかのぼって 1 日ずつ進める。

状態は .state/rpg.json に保存し、workflow の最後のステップで master にコミットする。
乱数は「日付＋周回数」を種にするので、同じ日に何度実行しても結果は同じ（その日の分が処理済みなら進めない）。

環境変数:
  GIST_PAT       Gist の作成（--create-gist）・更新と、コントリビューション数の取得に使う
  GITHUB_TOKEN   GIST_PAT でコントリビューション数を取れなかったときに使う
  GIST_ID_RPG    書き込む Gist の ID（未設定なら Gist は更新しない。workflow ではステップごと実行しない）
  RPG_USER       コントリビューションを数える GitHub ユーザー（既定は GITHUB_REPOSITORY_OWNER）

使い方:
  python scripts/rpg.py --dry-run --contributions 3   # 記録せずに 1 日分を試す
  GIST_PAT=xxx python scripts/rpg.py --create-gist    # 冒険を始めるための Gist を作る（ID を GIST_ID_RPG に登録する）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time as time_module
import urllib.error
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402

WORLD_PATH = common.ROOT / "rpg_world.json"
STATE_PATH = common.ROOT / ".state" / "rpg.json"
FILENAME = "hero-adventure.txt"
GIST_DESCRIPTION = "勇者の冒険 — コミットするほど勇者が進むテキスト RPG"
JOURNAL_DAYS = 30
CATCH_UP_DAYS = 7  # 実行されなかった日をさかのぼって進める最大の日数
# カードの「昨日」に優先して出す出来事（前ほど優先）
HIGHLIGHTS = ("クリア", "力尽きた", "敗れた", "討ち取った", "レベルが上がった", "伝説", "買った")
MAP_CELLS = 24
GRAPHQL = "https://api.github.com/graphql"
CONTRIB_QUERY = """query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions totalIssueContributions totalPullRequestContributions
      totalPullRequestReviewContributions restrictedContributionsCount
    }
  }
}"""


# ---- 世界と状態 ---------------------------------------------------------------------
def load_world(path: Path | None = None) -> dict:
    world = json.loads((path or WORLD_PATH).read_text(encoding="utf-8"))
    start = 0
    for area in world["areas"]:
        area["start"] = start          # 町のマス
        area["end"] = start + area["length"] - 1  # ボスのマス
        start += area["length"]
    world["total"] = start
    return world


def new_hero(world: dict) -> dict:
    h = world["hero"]
    return {"level": 1, "exp": 0, "max_hp": h["max_hp"], "hp": h["max_hp"], "base_atk": h["atk"],
            "base_def": h["def"], "gold": h["gold"], "potions": h["potions"], "weapon": None, "armor": None}


def new_state(world: dict) -> dict:
    return {"day": 0, "lap": 1, "lap_day": 0, "pos": 0, "boss_cleared": [], "hero": new_hero(world),
            "kills": 0, "deaths": 0, "last_date": None, "last": None, "journal": [], "records": []}


def load_state(world: dict, path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return new_state(world)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} が JSON として読めません（マージ衝突などを確認してください）: {e}")
    # 項目を足したり rpg_world.json を縮めたりしても動くように、足りない項目を補い、世界の外に出ないようにする
    state = {**new_state(world), **loaded}
    state["hero"] = {**new_hero(world), **state["hero"]}
    state["pos"] = min(state["pos"], world["total"] - 1)
    names = {a["name"] for a in world["areas"]}
    state["boss_cleared"] = [n for n in state["boss_cleared"] if n in names]
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def area_at(world: dict, pos: int) -> dict:
    for area in world["areas"]:
        if pos <= area["end"]:
            return area
    return world["areas"][-1]


def atk(hero: dict) -> int:
    return hero["base_atk"] + (hero["weapon"]["power"] if hero["weapon"] else 0)


def defense(hero: dict) -> int:
    return hero["base_def"] + (hero["armor"]["power"] if hero["armor"] else 0)


def steps_for(world: dict, contributions: int) -> int:
    """step_rule の [下限, 歩数] のうち、当てはまる最も大きいもの。"""
    return max(s for lo, s in world["step_rule"] if contributions >= lo)


# ---- 1 日分を進める ------------------------------------------------------------------
class Day:
    """1 日分の出来事を記録しながら進める。"""

    def __init__(self, world: dict, state: dict, rng: random.Random):
        self.world, self.state, self.rng = world, state, rng
        self.hero = state["hero"]
        self.events: list[str] = []

    def log(self, text: str) -> None:
        self.events.append(text)

    # 戦闘 ----------------------------------------------------------------------------
    def damage(self, attack: int, guard: int) -> int:
        return max(1, attack - guard // 2 + self.rng.randint(-2, 2))

    def battle(self, enemy: dict, boss: bool = False) -> bool:
        """勝てば True。HP が 0 になったら False（呼び出し元で町に戻す）。"""
        hero, hp = self.hero, enemy["hp"]
        while True:
            hp -= self.damage(atk(hero), enemy["def"])
            if hp <= 0:
                break
            hero["hp"] -= self.damage(enemy["atk"], defense(hero))
            if hero["hp"] <= 0:
                if hero["potions"]:  # 倒れる前に回復薬を使う
                    hero["potions"] -= 1
                    hero["hp"] = max(1, int(hero["max_hp"] * self.world["potion"]["heal_rate"]))
                    continue
                hero["hp"] = 0
                return False
        hero["gold"] += enemy["gold"]
        self.state["kills"] += 1
        mark = "を倒した！" if not boss else "を討ち取った！！"
        self.log(f"{enemy['name']}{mark}" + (f" (+{enemy['exp']}EXP)" if enemy["exp"] else ""))
        self.gain_exp(enemy["exp"])
        return True

    def gain_exp(self, exp: int) -> None:
        hero, up = self.hero, self.world["level_up"]
        hero["exp"] += exp
        while hero["exp"] >= up["exp_base"] * hero["level"] ** 2:
            hero["exp"] -= up["exp_base"] * hero["level"] ** 2
            hero["level"] += 1
            hero["max_hp"] += up["hp"]
            hero["base_atk"] += up["atk"]
            hero["base_def"] += up["def"]
            hero["hp"] = hero["max_hp"]
            self.log(f"レベルが上がった！ Lv.{hero['level']}")

    def fall(self) -> None:
        """HP が 0 になった。そのエリアの町に戻され、所持金が半分になる。"""
        area = area_at(self.world, self.state["pos"])
        self.state["pos"] = area["start"]
        self.state["deaths"] += 1
        self.hero["gold"] //= 2
        self.log(f"力尽きた… {area['town']}に運ばれた（所持金が半分に）")
        self.town(area)  # 町で回復し、残ったお金で買い物をする

    # マスのイベント ---------------------------------------------------------------------
    def town(self, area: dict) -> None:
        hero = self.hero
        hero["hp"] = hero["max_hp"]
        bought = []
        for item in area["shop"]:
            current = hero[item["slot"]]
            if (current is None or current["power"] < item["power"]) and hero["gold"] >= item["price"]:
                hero["gold"] -= item["price"]
                hero[item["slot"]] = {"name": item["name"], "power": item["power"]}
                bought.append(item["name"])
        self.buy_potions(keep=2)
        self.log(f"{area['town']}で休んだ" + (f"、{'と'.join(bought)}を買った" if bought else ""))

    def buy_potions(self, keep: int) -> int:
        hero, price, n = self.hero, self.world["potion"]["price"], 0
        while hero["potions"] < keep and hero["gold"] >= price:
            hero["gold"] -= price
            hero["potions"] += 1
            n += 1
        return n

    def random_event(self, area: dict) -> bool:
        """通常のマスのイベント。力尽きたら False。"""
        kinds, weights = zip(*self.world["events"].items())
        kind = self.rng.choices(kinds, weights)[0]
        hero = self.hero
        if kind == "battle":
            enemy = self.rng.choice(area["enemies"])
            if not self.battle(enemy):
                self.log(f"{enemy['name']}に敗れた")
                return False
        elif kind == "treasure":
            if self.rng.random() < 0.3:
                hero["potions"] += 1
                self.log("宝箱から回復薬を見つけた")
            else:
                gold = self.rng.randint(10, 30) * (self.world["areas"].index(area) + 1)
                hero["gold"] += gold
                self.log(f"宝箱から{gold}Gを見つけた")
        elif kind == "merchant":
            n = self.buy_potions(keep=3)
            self.log("旅の商人に出会った" + (f"、回復薬を{n}個買った" if n else ""))
        elif kind == "rare":
            index = self.world["areas"].index(area)
            choices = [r for r in self.world["rare"] if r.get("min_area", 0) <= index]
            rare = self.rng.choice(choices)
            if "item" in rare:
                item = rare["item"]
                current = hero[item["slot"]]
                if current is None or current["power"] < item["power"]:
                    hero[item["slot"]] = {"name": item["name"], "power": item["power"]}
            for key, stat in (("max_hp", "max_hp"), ("atk", "base_atk"), ("def", "base_def")):
                hero[stat] += rare.get(key, 0)
            self.log(rare["text"])
        else:
            self.log(self.rng.choice(area["scenery"]))
        return True

    def step(self) -> bool:
        """1 歩進む。力尽きたら False（その日はそこで終わり）。"""
        state = self.state
        area = area_at(self.world, state["pos"])
        if state["pos"] == area["end"] and area["name"] not in state["boss_cleared"]:
            return self.fight_boss(area)
        state["pos"] += 1
        area = area_at(self.world, state["pos"])
        if state["pos"] == area["start"]:
            self.town(area)
            return True
        if state["pos"] == area["end"]:
            return self.fight_boss(area)
        return self.random_event(area)

    def fight_boss(self, area: dict) -> bool:
        boss = area["boss"]
        if not self.battle(boss, boss=True):
            self.log(f"{boss['name']}に敗れた")
            return False
        self.state["boss_cleared"].append(area["name"])
        if area is self.world["areas"][-1]:
            self.clear()
        return True

    def clear(self) -> None:
        """魔王を倒した。記録を残して、最初からもう一度挑戦する（レベルと装備はリセット）。"""
        state = self.state
        state["records"].append({"lap": state["lap"], "days": state["lap_day"], "deaths": state["deaths"],
                                 "level": self.hero["level"], "cleared_on": state["last_date"]})
        self.log(f"魔王を倒し、世界に平和が戻った！ {state['lap']}周目クリア（{state['lap_day']}日）")
        state.update({"lap": state["lap"] + 1, "lap_day": 0, "pos": 0, "boss_cleared": [], "kills": 0,
                      "deaths": 0, "hero": new_hero(self.world)})
        self.hero = state["hero"]


def advance(world: dict, state: dict, today: date, contributions: int) -> bool:
    """today の分を進める。処理済み（last_date 以前の日付）なら何もせず False。"""
    if state["last_date"] and today.isoformat() <= state["last_date"]:
        return False
    rng = random.Random(f"{today.isoformat()}-{state['lap']}")
    state["day"] += 1
    state["lap_day"] += 1
    state["last_date"] = today.isoformat()
    day = Day(world, state, rng)
    steps = steps_for(world, contributions)
    if state["lap_day"] == 1 and state["pos"] == 0:  # 旅立ちの日は最初の町で支度する
        day.town(world["areas"][0])
    if steps == 0:
        hero = state["hero"]
        hero["hp"] = min(hero["max_hp"], hero["hp"] + int(hero["max_hp"] * world["rest_heal_rate"]))
        day.log(rng.choice(world["rest"]))
    for _ in range(steps):
        if not day.step():
            day.fall()
            break
        if state["lap_day"] == 0:  # クリアして次の周回に入った
            break
    state["last"] = {"date": today.isoformat(), "contributions": contributions, "steps": steps, "events": day.events}
    state["journal"] = ([{"date": today.isoformat(), "contributions": contributions, "text": " / ".join(day.events)}]
                        + state["journal"])[:JOURNAL_DAYS]
    return True


# ---- 表示 ---------------------------------------------------------------------------
def fit(text: str, limit: int = common.LINE_MAX) -> str:
    """表示幅が limit を超えたら末尾を「…」にして詰める。"""
    if common.width(text) <= limit:
        return text
    while common.width(text) > limit - 1:
        text = text[:-1]
    return text + "…"


def map_line(world: dict, state: dict) -> str:
    total = world["total"] - 1
    here = round(state["pos"] / total * (MAP_CELLS - 1))
    path = "".join("━" if i < here else "─" for i in range(MAP_CELLS))
    path = path[:here] + "🧙" + path[here + 1:]
    remaining = total - state["pos"]
    return f"🏠{path}🏰 魔王城まで{remaining}歩"


def teaser(world: dict, state: dict) -> str:
    hero = state["hero"]
    if state["lap_day"] == 0:
        return "新たな勇者が旅立とうとしている…"
    if hero["hp"] < hero["max_hp"] * 0.3:
        return "HP が心もとない…"
    area = area_at(world, state["pos"])
    if state["pos"] == area["end"] and area["name"] not in state["boss_cleared"]:
        return f"{area['boss']['name']}が行く手を阻んでいる…"
    if state["pos"] + 1 == area["end"]:
        return f"{area['boss']['name']}の気配がする…"
    nxt = area_at(world, state["pos"] + 1)
    if nxt is not area and state["pos"] + 1 == nxt["start"]:
        return f"{nxt['town']}が見えてきた"
    rng = random.Random(f"teaser-{state['last_date']}")
    return rng.choice(area["scenery"]) + "…"


def highlights(events: list[str], n: int = 2) -> list[str]:
    """カードに出す出来事を n 個選ぶ（力尽きた・クリアなどを優先し、起きた順に並べる）。"""
    def rank(i: int) -> int:
        return next((r for r, key in enumerate(HIGHLIGHTS) if key in events[i]), len(HIGHLIGHTS))
    return [events[i] for i in sorted(sorted(range(len(events)), key=rank)[:n])]


def build_text(world: dict, state: dict) -> str:
    hero, area = state["hero"], area_at(world, state["pos"])
    hp_bar = common.bar(hero["hp"], hero["max_hp"], 10).replace(" ", "░")
    last = state["last"] or {"contributions": 0, "events": ["冒険の始まり"]}
    summary = "、".join(highlights(last["events"])) if last["events"] else "何も起きなかった"
    lines = [
        f"⚔️ Day {state['day']}  {world['title']} Lv.{hero['level']} ── {area['emoji']} {area['name']}",
        map_line(world, state),
        f"HP {hp_bar} {hero['hp']}/{hero['max_hp']}  攻{atk(hero)} 防{defense(hero)}  {hero['gold']}G",
        f"昨日({last['contributions']} contrib): {summary}",
        f"今日: {teaser(world, state)}",
    ]
    card = [fit(line) for line in lines]
    weapon = hero["weapon"]["name"] if hero["weapon"] else "なし"
    armor = hero["armor"]["name"] if hero["armor"] else "なし"
    detail = [
        "",
        "── ステータス ──",
        f"{state['lap']}周目 {state['lap_day']}日目 / 通算 {state['day']}日",
        f"Lv.{hero['level']}  EXP {hero['exp']}/{world['level_up']['exp_base'] * hero['level'] ** 2}",
        f"武器: {weapon}  防具: {armor}  回復薬: {hero['potions']}",
        f"倒した敵: {state['kills']}  力尽きた回数: {state['deaths']}",
        f"倒したボス: {'、'.join(state['boss_cleared']) or 'まだいない'}",
        "",
        "── 冒険の記録（日付は進めた日、カッコ内はその前日のコントリビューション数）──",
    ]
    detail += [f"{j['date']} ({j['contributions']}) {j['text']}" for j in state["journal"]]
    if state["records"]:
        detail += ["", "── 歴代の勇者 ──"]
        detail += [f"{r['lap']}周目: {r['days']}日でクリア（Lv.{r['level']}、力尽きた回数 {r['deaths']}）"
                   for r in state["records"]]
    return "\n".join(card + detail) + "\n"


# ---- コントリビューション数 -----------------------------------------------------------
def post_graphql(token: str, body: dict) -> dict:
    """5xx・429・通信エラーは RETRY_WAITS に従って再試行する（401/403 などはすぐに諦める）。"""
    for wait in (*common.RETRY_WAITS, None):
        try:
            return common.api("POST", GRAPHQL, token, body)
        except urllib.error.HTTPError as e:
            if wait is None or not (e.code >= 500 or e.code == 429):
                raise
        except OSError:
            if wait is None:
                raise
        time_module.sleep(wait)
    raise AssertionError("unreachable")


def fetch_contributions(login: str, day: date, tokens: list[str]) -> int:
    """day（JST の 1 日）のコントリビューション数。どのトークンでも取れなければ FetchError。"""
    start = datetime.combine(day, time.min, tzinfo=common.JST)
    variables = {"login": login, "from": start.isoformat(),
                 "to": (start + timedelta(days=1) - timedelta(seconds=1)).isoformat()}
    errors = []
    for token in tokens:
        try:
            res = post_graphql(token, {"query": CONTRIB_QUERY, "variables": variables})
        except Exception as e:  # noqa: BLE001  トークンごとの失敗は次のトークンで再試行する
            errors.append(str(e))
            continue
        user = (res.get("data") or {}).get("user")
        if res.get("errors") or not user:
            errors.append(str(res.get("errors") or "user が見つからない"))
            continue
        return sum(user["contributionsCollection"].values())
    raise common.FetchError(f"コントリビューション数を取得できません: {'; '.join(errors) or 'トークンがありません'}")


def pending_days(state: dict, today: date) -> list[date]:
    """進める日の一覧（古い順）。last_date の翌日から today まで、最大 CATCH_UP_DAYS 日。"""
    if state["last_date"] is None:
        return [today]
    first = max(date.fromisoformat(state["last_date"]) + timedelta(days=1), today - timedelta(days=CATCH_UP_DAYS - 1))
    return [first + timedelta(days=i) for i in range((today - first).days + 1)]


def create_gist(world: dict) -> int:
    """冒険前の勇者のカードを中身にした public Gist を作り、ID を表示する（Actions ではジョブのサマリーにも出す）。"""
    if os.environ.get("GIST_ID_RPG"):
        raise SystemExit(f"GIST_ID_RPG が設定済み（{os.environ['GIST_ID_RPG']}）のため、Gist は作りません")
    token = os.environ.get("GIST_PAT")
    if not token:
        raise SystemExit("GIST_PAT を環境変数で指定してください")
    gist = common.create_gist(token, FILENAME, build_text(world, new_state(world)), GIST_DESCRIPTION)
    message = (f"Gist を作りました: {gist['html_url']}\n\n"
               f"Repository variable `GIST_ID_RPG` に `{gist['id']}` を登録してから、もう一度実行してください。")
    print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## 勇者の冒険の Gist\n\n{message}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="状態を保存せず、Gist も更新しない")
    ap.add_argument("--date", help="進める日付（既定は今日の JST）")
    ap.add_argument("--contributions", type=int, help="前日のコントリビューション数を指定する（取得しない）")
    ap.add_argument("--create-gist", action="store_true", help="冒険を始めるための Gist を作るだけ（冒険は進めない）")
    a = ap.parse_args()
    if a.create_gist and (a.dry_run or a.date or a.contributions is not None):
        ap.error("--create-gist は --dry-run / --date / --contributions と一緒に指定できません")

    world = load_world()
    if a.create_gist:
        return create_gist(world)
    state = load_state(world)
    today = date.fromisoformat(a.date) if a.date else datetime.now(timezone.utc).astimezone(common.JST).date()

    days = pending_days(state, today)
    advanced = 0
    if not days:
        print(f"{today} の分は処理済みのため進めません（表示だけ更新します）")
    elif a.contributions is not None:
        advanced += advance(world, state, today, a.contributions)
    else:
        login = os.environ.get("RPG_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER")
        if not login:
            raise SystemExit("RPG_USER か GITHUB_REPOSITORY_OWNER を環境変数で指定してください")
        tokens = [t for t in (os.environ.get("GIST_PAT"), os.environ.get("GITHUB_TOKEN")) if t]
        for day in days:  # 実行されなかった日があれば、古い日から 1 日ずつ進める
            try:
                contributions = fetch_contributions(login, day - timedelta(days=1), tokens)
            except common.FetchError as e:
                common.warn(f"{e}。{day} から先は進めません（次の実行で、この日からさかのぼって進めます）")
                break
            advanced += advance(world, state, day, contributions)
    if days and not advanced:
        return 0  # 1 日も進められなかった（Gist もそのまま）
    if advanced and not a.dry_run:
        save_state(state)

    content = build_text(world, state)
    print(content)
    if a.dry_run:
        return 0
    gist_id = os.environ.get("GIST_ID_RPG")
    if not gist_id:
        print("GIST_ID_RPG が未設定のため Gist の更新をスキップ")
        return 0
    token = os.environ.get("GIST_PAT")
    if not token:
        raise SystemExit("GIST_PAT を環境変数で指定してください")
    common.update_gist(gist_id, token, FILENAME, content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
