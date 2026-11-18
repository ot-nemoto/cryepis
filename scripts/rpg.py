#!/usr/bin/env python3
"""毎日ランダムに進むテキスト RPG。勇者ごとの Gist（最大 MAX_HEROES 人）を 1 日分ずつ進めて更新する。

勇者は heroes.json（勇者管理シート）に登録する。1 日に進む歩数は乱数で決まる（rpg_world.json の step_weights。
0 なら休息日）。1 歩ごとに戦闘・宝箱・商人などのイベントが起き、エリアの最後のボスを倒すと次のエリアへ進む。
魔王を倒すとクリアで、記録を残して最初からもう一度挑戦する。HP が 0 になったら、そのエリアの町に戻され、
所持金が半分になる。workflow が動かなかった日があれば、次の実行で最大 CATCH_UP_DAYS 日分までさかのぼって進める。

状態は .state/heroes.json（勇者の一覧）と .state/heroes/<id>.json（勇者ごとの状態）に保存し、
workflow の最後のステップで master にコミットする。
乱数は「日付＋周回数＋勇者の ID」を種にするので、同じ日に何度実行しても結果は同じ（その日の分が処理済みなら進めない）。

環境変数:
  GIST_PAT   勇者の Gist の作成・更新に使う（gist スコープの Classic PAT）

使い方:
  python scripts/rpg.py                          # 登録済みの勇者を全員 1 日分進めて Gist を更新する（毎日の workflow）
  python scripts/rpg.py --dry-run --steps 3      # 記録せずに 1 日分を試す
  GIST_PAT=xxx python scripts/rpg.py --new-game 名前   # 新しい勇者を登録し、Gist を作る
  python scripts/rpg.py --retire 名前または ID         # 勇者を引退させる（毎日の更新の対象から外れる）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402

WORLD_PATH = common.ROOT / "rpg_world.json"
HEROES_PATH = common.ROOT / ".state" / "heroes.json"
HERO_DIR = common.ROOT / ".state" / "heroes"
FILENAME = "hero-adventure.txt"
GIST_DESCRIPTION = "勇者の冒険 — 毎日ランダムに進むテキスト RPG"
MAX_HEROES = 6  # 同時に冒険できる（引退していない）勇者の数
NAME_WIDTH_MAX = 16  # 勇者の名前の表示幅（全角 = 2）
JOURNAL_DAYS = 30
CATCH_UP_DAYS = 7  # 実行されなかった日をさかのぼって進める最大の日数
# カードの「昨日」に優先して出す出来事（前ほど優先）
HIGHLIGHTS = ("クリア", "力尽きた", "敗れた", "討ち取った", "レベルが上がった", "伝説", "買った")
MAP_CELLS = 24


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


def new_state(world: dict, hero_id: str = "h1", name: str = "勇者") -> dict:
    return {"id": hero_id, "name": name, "day": 0, "lap": 1, "lap_day": 0, "pos": 0, "boss_cleared": [],
            "hero": new_hero(world), "kills": 0, "deaths": 0, "last_date": None, "last": None, "journal": [],
            "records": []}


def hero_state_path(hero_id: str) -> Path:
    return HERO_DIR / f"{hero_id}.json"


def load_state(world: dict, path: Path, hero_id: str = "h1", name: str = "勇者") -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return new_state(world, hero_id, name)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} が JSON として読めません（マージ衝突などを確認してください）: {e}")
    # 項目を足したり rpg_world.json を縮めたりしても動くように、足りない項目を補い、世界の外に出ないようにする
    state = {**new_state(world, hero_id, name), **loaded}
    state["hero"] = {**new_hero(world), **state["hero"]}
    state["pos"] = min(state["pos"], world["total"] - 1)
    names = {a["name"] for a in world["areas"]}
    state["boss_cleared"] = [n for n in state["boss_cleared"] if n in names]
    return state


def save_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---- 勇者管理シート（heroes.json）-----------------------------------------------------
def load_heroes(path: Path | None = None) -> list[dict]:
    path = path or HEROES_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))["heroes"]
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, KeyError) as e:
        raise SystemExit(f"{path} が読めません（マージ衝突などを確認してください）: {e!r}")


def save_heroes(heroes: list[dict], path: Path | None = None) -> None:
    path = path or HEROES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"heroes": heroes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_name(raw: str) -> str:
    """制御文字を除き、表示幅 NAME_WIDTH_MAX 以内に切り詰める。空なら SystemExit。"""
    name = "".join(c for c in raw if c.isprintable()).strip()
    while common.width(name) > NAME_WIDTH_MAX:
        name = name[:-1]
    name = name.strip()
    if not name:
        raise SystemExit("勇者の名前を指定してください")
    if re.fullmatch(r"h\d+", name):  # 引退の指定で ID と取り違えないように
        raise SystemExit(f"「{name}」は勇者の ID と紛らわしいので、名前には使えません")
    return name


def md_escape(text: str) -> str:
    """GitHub Actions のサマリー（Markdown）に名前をそのまま出さないよう、記号をエスケープする。"""
    return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~&])", r"\\\1", text)


def area_at(world: dict, pos: int) -> dict:
    for area in world["areas"]:
        if pos <= area["end"]:
            return area
    return world["areas"][-1]


def atk(hero: dict) -> int:
    return hero["base_atk"] + (hero["weapon"]["power"] if hero["weapon"] else 0)


def defense(hero: dict) -> int:
    return hero["base_def"] + (hero["armor"]["power"] if hero["armor"] else 0)


def roll_steps(world: dict, rng: random.Random) -> int:
    """step_weights（[歩数, 重み] の一覧）に従って、その日に進む歩数を乱数で決める。"""
    steps, weights = zip(*world["step_weights"])
    return rng.choices(steps, weights)[0]


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


def advance(world: dict, state: dict, today: date, steps: int | None = None) -> bool:
    """today の分を進める（steps を省くと乱数で決める）。処理済み（last_date 以前の日付）なら何もせず False。"""
    if state["last_date"] and today.isoformat() <= state["last_date"]:
        return False
    rng = random.Random(f"{today.isoformat()}-{state['lap']}-{state['id']}")
    state["day"] += 1
    state["lap_day"] += 1
    state["last_date"] = today.isoformat()
    day = Day(world, state, rng)
    if steps is None:
        steps = roll_steps(world, rng)
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
    state["last"] = {"date": today.isoformat(), "steps": steps, "events": day.events}
    state["journal"] = ([{"date": today.isoformat(), "steps": steps, "text": " / ".join(day.events)}]
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
    last = state["last"] or {"steps": 0, "events": ["冒険の始まり"]}
    summary = "、".join(highlights(last["events"])) if last["events"] else "何も起きなかった"
    lines = [
        f"⚔️ Day {state['day']}  {state['name']} Lv.{hero['level']} ── {area['emoji']} {area['name']}",
        map_line(world, state),
        f"HP {hp_bar} {hero['hp']}/{hero['max_hp']}  攻{atk(hero)} 防{defense(hero)}  {hero['gold']}G",
        f"昨日({last['steps']}歩): {summary}",
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
        "── 冒険の記録（日付は進めた日、カッコ内は進んだ歩数）──",
    ]
    detail += [f"{j['date']} ({j['steps']}歩) {j['text']}" for j in state["journal"]]
    if state["records"]:
        detail += ["", "── 歴代の勇者 ──"]
        detail += [f"{r['lap']}周目: {r['days']}日でクリア（Lv.{r['level']}、力尽きた回数 {r['deaths']}）"
                   for r in state["records"]]
    return "\n".join(card + detail) + "\n"


# ---- 実行 ---------------------------------------------------------------------------
def pending_days(state: dict, today: date) -> list[date]:
    """進める日の一覧（古い順）。last_date の翌日から today まで、最大 CATCH_UP_DAYS 日。"""
    if state["last_date"] is None:
        return [today]
    first = max(date.fromisoformat(state["last_date"]) + timedelta(days=1), today - timedelta(days=CATCH_UP_DAYS - 1))
    return [first + timedelta(days=i) for i in range((today - first).days + 1)]


def write_summary(text: str) -> None:
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def new_game(world: dict, raw_name: str, today: date) -> int:
    """新しい勇者を登録し、冒険前のカードを中身にした public Gist と状態を作る（冒険は翌日から進む）。"""
    token = os.environ.get("GIST_PAT")
    if not token:
        raise SystemExit("GIST_PAT を環境変数で指定してください")
    name = clean_name(raw_name)
    heroes = load_heroes()
    active = [h for h in heroes if h["active"]]
    if len(active) >= MAX_HEROES:
        raise SystemExit(f"勇者は最大 {MAX_HEROES} 人です。先に retire-hero で引退させてください")
    if any(h["name"] == name for h in active):
        raise SystemExit(f"勇者「{name}」はすでにいます")
    hero_id = f"h{max((int(h['id'][1:]) for h in heroes), default=0) + 1}"
    state = new_state(world, hero_id, name)
    gist = common.create_gist(token, FILENAME, build_text(world, state), GIST_DESCRIPTION)
    print(f"Gist を作りました: {gist['html_url']}")  # 以降の保存・コミットが失敗しても、孤立した Gist を探せるように
    heroes.append({"id": hero_id, "name": name, "gist_id": gist["id"], "gist_url": gist["html_url"],
                   "created": today.isoformat(), "active": True})
    save_state(state, hero_state_path(hero_id))
    save_heroes(heroes)
    write_summary(f"## 勇者「{md_escape(name)}」({hero_id}) が旅立ちます\n\nGist: {gist['html_url']}\n\n"
                  "冒険は次回の毎日の実行から進みます。プロフィールの **Customize your pins** で Gist を Pin できます。")
    return 0


def retire(key: str) -> int:
    """ID か名前で勇者を引退させる（Gist と状態は残す）。"""
    heroes = load_heroes()
    active = [h for h in heroes if h["active"]]
    # ID を優先して照合する（名前が ID と同じ勇者は作れない）
    for h in [h for h in active if h["id"] == key] + [h for h in active if h["name"] == key]:
        h["active"] = False
        save_heroes(heroes)
        write_summary(f"勇者「{md_escape(h['name'])}」({h['id']}) を引退させました（Gist: {h['gist_url']}）")
        return 0
    raise SystemExit(f"引退させられる勇者「{key}」がいません（ID か名前で、現役の勇者を指定してください）")


def run_hero(world: dict, hero: dict, today: date, steps: int | None, dry_run: bool, token: str | None) -> None:
    path = hero_state_path(hero["id"])
    if not path.exists():  # 登録済みの勇者の状態が無いのは異常。黙って最初からにしない
        raise SystemExit(f"{path} がありません（削除やマージで消えた可能性があります）")
    state = load_state(world, path, hero["id"], hero["name"])
    days = pending_days(state, today)
    if not days:
        print(f"{hero['name']}: {today} の分は処理済みのため進めません（表示だけ更新します）")
    elif steps is not None:
        advance(world, state, today, steps)
    else:
        for day in days:  # 実行されなかった日があれば、古い日から 1 日ずつ進める
            advance(world, state, day)
    if days and not dry_run:
        save_state(state, path)
    content = build_text(world, state)
    print(content)
    if not dry_run:
        common.update_gist(hero["gist_id"], token, FILENAME, content)


def advance_all(world: dict, today: date, steps: int | None, dry_run: bool) -> int:
    """現役の勇者を全員進める。1 人が失敗しても残りは続け、1 人でも失敗したら終了コード 1。"""
    heroes = [h for h in load_heroes() if h["active"]]
    if not heroes:
        print("現役の勇者がいません（new-game で作れます）")
        return 0
    token = os.environ.get("GIST_PAT")
    if not token and not dry_run:
        raise SystemExit("GIST_PAT を環境変数で指定してください")
    failed = 0
    for hero in heroes:
        try:
            run_hero(world, hero, today, steps, dry_run, token)
        except (Exception, SystemExit) as e:  # noqa: BLE001  勇者ごとに失敗を隔離する
            failed += 1
            common.warn(f"{hero['name']}（{hero['id']}）を進められませんでした: {e}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="状態を保存せず、Gist も更新しない")
    ap.add_argument("--date", help="進める日付（既定は今日の JST）")
    ap.add_argument("--steps", type=int, help="その日に進む歩数を指定する（乱数で決めない）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--new-game", metavar="NAME", help="新しい勇者を登録し、Gist を作る（冒険は進めない）")
    mode.add_argument("--retire", metavar="NAME_OR_ID", help="勇者を引退させる")
    a = ap.parse_args()
    if (a.new_game is not None or a.retire is not None) and (a.dry_run or a.date or a.steps is not None):
        ap.error("--new-game / --retire は --dry-run / --date / --steps と一緒に指定できません")

    world = load_world()
    today = date.fromisoformat(a.date) if a.date else datetime.now(timezone.utc).astimezone(common.JST).date()
    if a.new_game is not None:
        return new_game(world, a.new_game, today)
    if a.retire is not None:
        return retire(a.retire)
    return advance_all(world, today, a.steps, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
