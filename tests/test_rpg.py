import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import game  # noqa: E402
import rpg  # noqa: E402
import store  # noqa: E402
import view  # noqa: E402
import website  # noqa: E402

WORLD = rpg.load_world()
DAY = date.fromisoformat(WORLD["season"]["start"])


def play(days, hero_id="h1"):
  state = game.new_state(WORLD, hero_id)
  for i in range(days):
    game.advance(WORLD, state, DAY + timedelta(days=i))
  return state


def load(hero_id):
  return game.load_state(WORLD, store.hero_state_path(hero_id))


class DisplayTest(unittest.TestCase):
  def test_text_starts_with_five_summary_lines(self):
    states = [game.new_state(WORLD, "h1", "あ" * 8)] + [play(n, f"h{n}") for n in (1, 10, 40, 90)]
    for state in states:
      lines = view.build_text(WORLD, state).splitlines()
      self.assertEqual(lines[5], "")  # 概要の 5 行のあとに詳細が続く
      self.assertTrue(lines[0].startswith("⚔️ "))
      self.assertTrue(lines[1].startswith("📍 "))
      self.assertTrue(lines[3].startswith("装備: "))
      self.assertTrue(lines[4].startswith("今日: "))

  def test_gear_shows_rares_first(self):
    world = {
      **WORLD,
      "items": {
        **WORLD["items"],
        "x_epic": {
          "id": "x_epic",
          "name": "試しの兜",
          "slot": "helmet",
          "rarity": "epic",
          "def": 9,
          "towns": ["edo"],
        },
      },
    }
    state = game.new_state(world)
    state["inventory"] = {"iron_sword": 1, "wooden_helm": 1, "x_epic": 1, "steel_shield": 1}
    self.assertEqual(view.gear_text(world, state), "★★★試しの兜/★★大鎧/★打刀")

  def test_quest_marks_are_not_rare_items(self):
    events = [
      "アウレアの口入れ屋で依頼を受けた: ★ 王家の書状（カエルムへ荷物を届ける）",
      "送り犬を倒した (+9EXP)",
      "宝箱から★「木の剣」を手に入れた",
      "宝箱から★★「狩人の短剣」を手に入れた！",
    ]
    self.assertEqual(view.highlights(events, 1), ["宝箱から★★「狩人の短剣」を手に入れた！"])

  def test_detail_shows_the_current_quest(self):
    state = play(1)
    t = next(x for x in WORLD["quests"] if x["type"] == "hunt")
    q = game.make_quest(WORLD, t, "edo", DAY, game.random.Random(0), 1)
    state["quest"] = q
    text = view.build_text(WORLD, state)
    self.assertIn("── 受けている依頼 ──", text)
    self.assertIn(q["text"], text)

  def test_card_before_the_journey(self):
    lines = view.build_text(WORLD, game.new_state(WORLD, "h1", "アリス")).splitlines()
    self.assertEqual(lines[0], "⚔️ アリス Lv.1  旅立ち前 0日目")
    self.assertIn("旅立とうとしている", lines[4])

  def test_detail_lists_items_and_past_seasons(self):
    state = play(3)
    game.carried(state).append({"id": "steel_sword", "skill": None})
    state["seasons"].append(
      {
        "season": 0,
        "days": 90,
        "level": 20,
        "quests_done": 30,
        "quests_failed": 2,
        "kills": 140,
        "deaths": 1,
        "rares": ["steel_sword"],
        "ended_on": "2026-11-30",
      }
    )
    text = view.build_text(WORLD, state)
    self.assertIn("★★業物の太刀", text)
    self.assertIn("シーズン0: Lv.20", text)

  def test_board_links_to_the_site(self):
    with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r"}, clear=True):
      text = rpg.build_board(WORLD, [({"id": "h1", "name": "アリス"}, play(1))], DAY)
    self.assertIn("詳細: https://o.github.io/r/heroes/h1.html", text)
    self.assertNotIn("MAP:", text)  # リポジトリの MAP のページはない

  def test_board_card_fits_and_ranks(self):
    names = ["アリス", "ボブ", "ゴールドヒルデガルト", "あ" * 8]
    entries = [({"id": f"h{i}", "name": n}, play(30, f"h{i}")) for i, n in enumerate(names)]
    today = DAY + timedelta(days=29)
    lines = rpg.build_board(WORLD, entries, today).splitlines()
    for line in lines[:5]:
      self.assertLessEqual(common.width(line), common.LINE_MAX, line)
    self.assertEqual(lines[5], "")
    first, last = game.season_range(WORLD, 1)
    self.assertEqual(lines[0], f"🏆 シーズン1  Day 30/{(last - first).days + 1}  もののふ4人")
    self.assertTrue(lines[1].startswith("Lv   🥇"))
    self.assertTrue(lines[2].startswith("討伐 🥇"))
    self.assertTrue(lines[4].startswith("📰 "))
    text = "\n".join(lines)
    self.assertIn("ゴールドヒルデガルト", text)  # 本文は名前を切り詰めない
    self.assertIn(
      f"詳細: {rpg.page_url('h0')}", text
    )  # もののふごとの欄と、詳細のページへのリンク（Actions では URL になる）
    self.assertIn("■ アリス  Lv.", text)

  def test_board_before_the_season_and_for_heroes_not_started(self):
    entries = [({"id": "h1", "name": "アリス"}, game.new_state(WORLD, "h1", "アリス"))]
    lines = rpg.build_board(WORLD, entries, DAY - timedelta(days=3)).splitlines()
    self.assertIn("開幕前", lines[0])
    self.assertIn("今日も、もののふたちは冒険中", lines[4])

  def test_same_level_gets_the_same_medal(self):
    entries = []
    for i, exp in enumerate((5, 30, 10)):
      state = play(1, f"h{i}")
      state["hero"].update(level=13, exp=exp)
      entries.append(({"id": f"h{i}", "name": f"もののふ{i}"}, state))
    line = rpg.build_board(WORLD, entries, DAY).splitlines()[1]
    self.assertEqual(line.count("🥇"), 3, line)

  def test_ranked_shares_rank_on_ties(self):
    rows = [({"name": n}, {"v": v}) for n, v in (("a", 5), ("b", 7), ("c", 5), ("d", 1))]
    self.assertEqual(
      [(r, h["name"]) for r, h, _ in view.ranked(rows, lambda s: s["v"])], [(1, "b"), (2, "a"), (2, "c"), (4, "d")]
    )

  def test_board_shows_past_season_champions(self):
    state = play(3)
    state["seasons"] = [
      {
        "season": 1,
        "days": 90,
        "level": 22,
        "quests_done": 30,
        "quests_failed": 1,
        "kills": 150,
        "deaths": 0,
        "rares": [],
        "ended_on": "2027-02-28",
      }
    ]
    text = rpg.build_board(WORLD, [({"id": "h1", "name": "アリス"}, state)], DAY)
    self.assertIn("シーズン1: 最高レベル アリス Lv.22  討伐数 アリス 150体", text)

  def test_highlights_prefer_important_events_and_merge_level_ups(self):
    events = ["小鬼を倒した", "レベルが上がった！ Lv.2", "レベルが上がった！ Lv.3", "依頼達成！x", "景色"]
    self.assertEqual(view.highlights(events), ["レベルが上がった！ Lv.3", "依頼達成！x"])
    self.assertEqual(
      view.highlights(["a", "宝箱から★★「蒼炎の剣」を手に入れた！", "力尽きた…"]),
      ["宝箱から★★「蒼炎の剣」を手に入れた！", "力尽きた…"],
    )

  def test_fit(self):
    self.assertEqual(view.fit("abc", 5), "abc")
    self.assertEqual(common.width(view.fit("あ" * 30)), common.LINE_MAX)

  def test_pending_days(self):
    state = game.new_state(WORLD)
    self.assertEqual(rpg.pending_days(state, DAY), [DAY])
    state["last_date"] = (DAY - timedelta(days=3)).isoformat()
    self.assertEqual(rpg.pending_days(state, DAY), [DAY - timedelta(days=2), DAY - timedelta(days=1), DAY])
    state["last_date"] = (DAY - timedelta(days=30)).isoformat()
    self.assertEqual(len(rpg.pending_days(state, DAY)), rpg.CATCH_UP_DAYS)
    state["last_date"] = DAY.isoformat()
    self.assertEqual(rpg.pending_days(state, DAY), [])


class HeroesTest(unittest.TestCase):
  """もののふ管理シート（heroes.json）・状態・詳細のページを一時ディレクトリに向けて、main を動かす。"""

  def setUp(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.dir = Path(tmp.name)
    for module, target, value in (
      (store, "HEROES_PATH", self.dir / "heroes.json"),
      (store, "HERO_DIR", self.dir / "heroes"),
      (rpg, "BOARD_PATH", self.dir / "board.json"),
      (store, "GUILD_PATH", self.dir / "guild.json"),
    ):
      patcher = mock.patch.object(module, target, value)
      patcher.start()
      self.addCleanup(patcher.stop)
    # Gist を作るのは番付だけ（毎日の実行の最初）
    patcher = mock.patch.object(common, "create_gist", return_value={"id": "board", "html_url": "https://g/board"})
    self.create_board = patcher.start()
    self.addCleanup(patcher.stop)
    patcher = mock.patch.object(common, "update_gist")
    self.update_gist = patcher.start()
    self.addCleanup(patcher.stop)

  def run_main(self, argv, env=None):
    with (
      mock.patch.object(sys, "argv", ["rpg.py", *argv]),
      mock.patch.dict("os.environ", env or {}, clear=True),
      mock.patch("sys.stdout"),
    ):
      return rpg.main()

  def new_game(self, name):
    return self.run_main(["--new-game", name])

  def daily(self, day="2026-12-01"):
    return self.run_main(["--date", day], {"GIST_PAT": "p"})

  def test_clean_name(self):
    self.assertEqual(rpg.clean_name("  アリス\n"), "アリス")
    self.assertLessEqual(common.width(rpg.clean_name("あ" * 30)), rpg.NAME_WIDTH_MAX)
    self.assertEqual(rpg.clean_name("あ" * 20), "あ" * 16)  # 全角は 16 文字まで
    self.assertEqual(rpg.clean_name("a" * 40), "a" * 32)  # 半角は 32 文字まで

  def test_long_names(self):
    # 表示幅 32 の名前でも、番付のカードの出来事が読める。もののふの文章は詰めないので、名前も Lv・日も全部出る
    name = "あいうえおかきくけこさしすせそた"
    world = WORLD
    state = game.new_state(world, "h1", name)
    game.advance(world, state, DAY)
    head = view.build_text(world, state).splitlines()[0]
    self.assertEqual(head, f"⚔️ {name} Lv.{state['hero']['level']}  S{state['season']} {state['season_day']}日目")
    hero = {"id": "h1", "name": name}
    news = view.news([(hero, state)], DAY, rpg.BOARD_NAME_WIDTH)
    self.assertTrue(news[0].startswith(view.shorten(name, rpg.BOARD_NAME_WIDTH) + ": "), news)
    self.assertLessEqual(common.width(news[0].split(": ")[0]), rpg.BOARD_NAME_WIDTH)
    with self.assertRaises(SystemExit):
      rpg.clean_name(" \t ")
    with self.assertRaises(SystemExit):
      rpg.clean_name("h12")  # ID と紛らわしい

  def test_md_escape(self):
    self.assertEqual(rpg.md_escape("[x](u)<b>"), r"\[x\]\(u\)\<b\>")

  def test_new_game_registers_hero_without_a_gist(self):
    self.assertEqual(self.new_game("アリス"), 0)  # GIST_PAT は要らない
    self.create_board.assert_not_called()
    (hero,) = store.load_heroes()
    self.assertEqual(hero, {"id": "h1", "name": "アリス", "created": hero["created"], "active": True})
    self.assertEqual(load("h1")["name"], "アリス")
    self.assertFalse(
      (rpg.common.ROOT / "heroes" / "h1.md").exists()
    )  # 詳細のページはリポジトリに書かない（サイトで見る）

  def test_new_game_rejects_bad_names_and_options(self):
    with self.assertRaises(SystemExit):
      self.new_game(" ")
    with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
      self.run_main(["--new-game", "アリス", "--dry-run"])
    self.assertEqual(store.load_heroes(), [])

  def test_new_game_rejects_duplicates_and_more_than_max(self):
    self.new_game("アリス")
    with self.assertRaises(SystemExit):
      self.new_game("アリス")
    for i in range(2, rpg.MAX_HEROES + 1):
      self.new_game(f"もののふ{i}")
    with self.assertRaises(SystemExit):
      self.new_game("あふれたもののふ")
    self.assertEqual(len(store.load_heroes()), rpg.MAX_HEROES)

  def test_retire_frees_a_slot_and_ids_are_not_reused(self):
    for i in range(1, rpg.MAX_HEROES + 1):
      self.new_game(f"もののふ{i}")
    self.run_main(["--retire", "もののふ2"])
    self.assertEqual([h["active"] for h in store.load_heroes()], [True, False] + [True] * (rpg.MAX_HEROES - 2))
    self.new_game("新人")
    self.assertEqual(store.load_heroes()[-1]["id"], f"h{rpg.MAX_HEROES + 1}")
    with self.assertRaises(SystemExit):
      self.run_main(["--retire", "もののふ2"])  # 引退済み

  def test_daily_advances_active_heroes_and_only_the_board_gist(self):
    self.new_game("アリス")
    self.new_game("ボブ")
    self.new_game("引退者")
    self.run_main(["--retire", "h3"])
    self.assertEqual(self.daily(), 0)
    # Gist は番付の 1 つだけ（最初の実行で作られ、board.json に記録される）
    self.assertEqual(self.create_board.call_count, 1)
    self.assertEqual(self.create_board.call_args.args[1], rpg.BOARD_FILENAME)
    self.update_gist.assert_not_called()
    self.assertEqual(rpg.load_board()["gist_id"], "board")
    board = self.create_board.call_args.args[2]
    self.assertIn("■ アリス", board)
    self.assertNotIn("引退者", board)
    self.assertEqual((load("h1")["day"], load("h3")["day"]), (1, 0))  # 引退者は進まない

  def test_site_url_for_a_user_pages_repo(self):
    with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "Alice/alice.github.io"}, clear=True):
      self.assertEqual(rpg.site_url(), "https://alice.github.io/")
    with mock.patch.dict("os.environ", {}, clear=True):
      self.assertEqual(rpg.hero_link("h1"), "サイトのもののふのページ")  # サイトの URL が分からなければ、URL は出さない

  def test_page_url_points_to_the_site(self):
    with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "O/r"}, clear=True):
      self.assertEqual(rpg.page_url("h1"), "https://o.github.io/r/heroes/h1.html")  # サイトのもののふの詳細
    with mock.patch.dict("os.environ", {}, clear=True):
      self.assertEqual(rpg.page_url("h1"), "heroes/h1.html")

  def test_daily_is_idempotent_and_catches_up(self):
    self.new_game("アリス")
    self.daily("2026-12-01")
    self.daily("2026-12-01")  # 同じ日は進まない
    self.assertEqual(load("h1")["day"], 1)
    self.daily("2026-12-04")  # 3 日空いた
    self.assertEqual(load("h1")["day"], 4)

  def test_one_failing_hero_does_not_stop_the_others(self):
    self.new_game("アリス")
    self.new_game("ボブ")
    store.hero_state_path("h1").write_text("{壊れた", encoding="utf-8")
    with mock.patch.object(common, "warn") as warn:
      self.assertEqual(self.daily(), 1)
    warn.assert_called_once()
    self.assertEqual(load("h2")["day"], 1)
    board = self.create_board.call_args.args[2]
    self.assertIn("■ ボブ", board)  # 進められたもののふだけで番付を作る
    self.assertNotIn("■ アリス", board)

  def test_a_hero_whose_state_cannot_be_saved_does_not_stop_the_others(self):
    self.new_game("アリス")
    self.new_game("ボブ")
    original = game.save_state

    def save(state, path):
      if state["id"] == "h1":
        raise OSError("ディスクがいっぱい")
      original(state, path)

    with mock.patch.object(game, "save_state", side_effect=save), mock.patch.object(common, "warn") as warn:
      self.assertEqual(self.daily(), 1)  # 失敗したもののふがいる
    warn.assert_called_once()
    self.assertIn("アリス", warn.call_args.args[0])
    self.assertIn("ディスクがいっぱい", warn.call_args.args[0])
    self.assertEqual(load("h2")["day"], 1)  # ボブは進んで、保存された
    self.assertEqual(load("h1")["day"], 0)  # アリスは進んでいない（次の実行でやり直す）
    board = self.create_board.call_args.args[2]
    self.assertIn("■ ボブ", board)  # 進められたもののふだけで番付を作る
    self.assertNotIn("■ アリス", board)

  def test_current_entries_skip_heroes_without_a_state_file(self):
    self.new_game("アリス")
    store.hero_state_path("h1").unlink()
    self.assertEqual(store.current_entries(WORLD), [])  # サイトの書き出しは、状態のないもののふを飛ばす

  def test_hero_that_failed_stays_on_the_heroes_page(self):
    self.new_game("アリス")
    self.new_game("ボブ")
    self.daily("2026-12-01")
    original = game.advance

    def fail_alice(world, state, *args):
      if state["id"] == "h1":
        raise RuntimeError("壊れた")
      return original(world, state, *args)

    with mock.patch.object(game, "advance", side_effect=fail_alice), mock.patch.object(common, "warn"):
      self.assertEqual(self.daily("2026-12-02"), 1)
    world = rpg.load_world()
    html = website.build_heroes(world, store.current_entries(world), date(2026, 12, 2))
    # 進められなかったもののふも、保存してある状態でもののふ一覧に載る。
    self.assertIn('<a href="heroes/h1.html">アリス</a>', html)
    self.assertIn('<a href="heroes/h2.html">ボブ</a>', html)

  def test_retired_heroes_keep_their_site_page(self):
    self.new_game("アリス")
    self.new_game("ボブ")
    self.run_main(["--retire", "h2"])
    world = rpg.load_world()
    self.assertEqual([h["id"] for h, _ in store.current_entries(world)], ["h1"])
    self.assertEqual([h["id"] for h, _ in store.current_entries(world, retired=True)], ["h2"])
    out = self.dir / "site"
    website.write_site(
      world, store.current_entries(world), DAY, game.new_guild(), out, store.current_entries(world, True)
    )
    self.assertTrue((out / "heroes" / "h2.html").exists())  # 引退したもののふの詳細も残る
    self.assertNotIn("ボブ", (out / "heroes.html").read_text(encoding="utf-8"))  # 一覧には出さない

  def test_only_heroes_advanced_today_fight_together(self):
    world = rpg.load_world()
    seen = {}

    def capture(world, st, day, actions, g, others=None):
      seen[st["id"]] = sorted(others or {})
      return True

    a, b = game.new_state(world, "a", "アリス"), game.new_state(world, "b", "ボブ")
    with mock.patch.object(game, "advance", side_effect=capture):
      rpg.advance_heroes(
        world, {"a": ({"id": "a"}, a, [date(2026, 12, 1)]), "b": ({"id": "b"}, b, [])}, date(2026, 12, 1), None, {}
      )
    self.assertEqual(seen, {"a": ["a"]})  # その日を進めない（保存しない）もののふとは一緒に戦わない

  def test_guild_preview_uses_the_heroes_levels(self):
    world = rpg.load_world()
    state = game.new_state(world, "h1", "アリス")
    state["hero"]["level"] = 14
    seen = []
    real = game.refresh_guild

    def capture(world, guild, d, season, towns=None):
      seen.append(guild.get("levels"))
      return real(world, guild, d, season, towns)

    with mock.patch.object(game, "refresh_guild", side_effect=capture):
      website.build_guild(world, [({"id": "h1"}, state)], date(2026, 12, 1), game.new_guild())
    self.assertEqual(seen, [{state["place"]: [14]}])  # 毎日の実行と同じく、町ごとの居場所と Lv で大物の依頼を抽選する

  def test_failed_heros_changes_to_the_guild_are_undone(self):
    guild = game.new_guild()
    state = game.new_state(rpg.load_world(), "h1", "アリス")
    state["hero"]["level"] = 99

    def take_then_fail(world, st, day, actions, g, others=None):
      game.refresh_guild(world, g, day, 1)
      next(e for es in g["towns"].values() for e in es).update(status="held", holder="h1")
      raise RuntimeError("壊れた")

    with mock.patch.object(game, "advance", side_effect=take_then_fail):
      errors = rpg.advance_heroes(
        rpg.load_world(), {"h1": ({"id": "h1"}, state, [date(2026, 12, 1)])}, date(2026, 12, 1), None, guild
      )
    self.assertIn("h1", errors)
    self.assertEqual(guild, game.new_guild())  # 高札は進める前のまま

  def test_broken_guild_file(self):
    store.GUILD_PATH.write_text("[]", encoding="utf-8")
    self.assertEqual(store.load_guild(), game.new_guild())
    store.GUILD_PATH.write_text("{", encoding="utf-8")
    with self.assertRaises(SystemExit):
      store.load_guild()

  def test_quests_are_first_come_first_served(self):
    for name in ("アリス", "ボブ", "キャロル", "デイブ", "イブ", "フランク"):
      self.new_game(name)
    for i in range(1, 6):
      self.daily(f"2026-12-0{i}")
    guild = json.loads((self.dir / "guild.json").read_text(encoding="utf-8"))
    held = [e for es in guild["towns"].values() for e in es if e["status"] == "held"]
    self.assertTrue(guild["towns"])
    for e in held:  # 受注中の依頼は、受けたもののふがちょうどその依頼を持っている
      self.assertEqual(load(e["holder"])["quest"]["id"], e["id"])
    ids = [e["id"] for e in held]
    self.assertEqual(len(ids), len(set(ids)))
    holding = [load(f"h{i}")["quest"] for i in range(1, 7)]
    for q in holding:  # もののふが持っている依頼（旅を除く）は、高札で受注中になっている
      if q and q["type"] != "patrol":
        self.assertIn(q["id"], ids)

  def test_old_guild_format_is_reposted(self):
    store.GUILD_PATH.write_text(json.dumps({"taken": {"2026-12-01-aurea-0": "h2"}}), encoding="utf-8")
    self.assertEqual(store.load_guild(), game.new_guild())

  def test_quest_of_a_retired_hero_goes_back_to_the_board(self):
    self.new_game("アリス")
    self.daily("2026-12-01")
    guild = json.loads(store.GUILD_PATH.read_text(encoding="utf-8"))
    entry = next(e for es in guild["towns"].values() for e in es if e["status"] == "open")
    entry.update(status="held", holder="h9")  # 引退したもののふ（現役の一覧にいない）が持っていた依頼
    store.GUILD_PATH.write_text(json.dumps(guild), encoding="utf-8")
    self.daily("2026-12-02")

    def find():
      towns = json.loads(store.GUILD_PATH.read_text(encoding="utf-8"))["towns"]
      return next(e for es in towns.values() for e in es if e["id"] == entry["id"])

    self.assertEqual(find()["status"], "failed")
    self.daily("2026-12-03")
    self.assertTrue(find()["returned"])  # 翌日、受付中に戻る（誰かがすぐ受けることもある）

  def test_board_is_updated_after_it_is_created(self):
    self.new_game("アリス")
    self.daily("2026-12-01")
    self.daily("2026-12-02")
    self.assertEqual(self.create_board.call_count, 1)
    (call,) = self.update_gist.call_args_list
    self.assertEqual(call.args[:3], ("board", "p", rpg.BOARD_FILENAME))
    self.assertEqual(call.kwargs["description"], "もののふ番付 シーズン1")

  def test_board_is_recreated_when_the_gist_is_gone(self):
    self.new_game("アリス")
    self.daily("2026-12-01")
    self.create_board.return_value = {"id": "board2", "html_url": "https://g/board2"}
    self.update_gist.side_effect = common.GistAPIError(404, "Gist API エラー 404")
    with mock.patch.object(common, "warn") as warn:
      self.assertEqual(self.daily("2026-12-02"), 0)
    warn.assert_called_once()
    self.assertEqual(rpg.load_board()["gist_id"], "board2")

  def test_board_other_errors_are_not_recreated(self):
    self.new_game("アリス")
    self.daily("2026-12-01")
    self.update_gist.side_effect = common.GistAPIError(403, "Gist API エラー 403")
    with mock.patch.object(common, "warn"):
      self.assertEqual(self.daily("2026-12-02"), 1)
    self.assertEqual(self.create_board.call_count, 1)
    self.assertEqual(rpg.load_board()["gist_id"], "board")

  def test_board_failure_fails_the_run_but_keeps_heroes(self):
    self.new_game("アリス")
    self.create_board.side_effect = SystemExit("Gist API エラー 403")
    with mock.patch.object(common, "warn") as warn:
      self.assertEqual(self.daily(), 1)
    warn.assert_called_once()
    self.assertEqual(load("h1")["day"], 1)
    self.assertIsNone(rpg.load_board())

  def test_daily_needs_pat(self):
    self.new_game("アリス")
    with self.assertRaises(SystemExit):
      self.run_main(["--date", "2026-12-01"])
    self.assertEqual(load("h1")["day"], 0)

  def test_retire_matches_id_first(self):
    self.new_game("アリス")
    self.new_game("ボブ")
    self.run_main(["--retire", "h2"])
    self.assertEqual([h["active"] for h in store.load_heroes()], [True, False])

  def test_missing_state_file_is_an_error_not_a_reset(self):
    self.new_game("アリス")
    store.hero_state_path("h1").unlink()
    with mock.patch.object(common, "warn") as warn:
      self.assertEqual(self.daily(), 1)
    self.create_board.assert_not_called()  # 進められたもののふがいなければ、番付も作らない
    warn.assert_called_once()
    self.assertFalse(store.hero_state_path("h1").exists())

  def test_no_heroes_is_a_noop(self):
    self.assertEqual(self.daily(), 0)
    self.create_board.assert_not_called()

  def test_map_option_is_gone(self):
    with self.assertRaises(SystemExit), mock.patch("sys.stderr"):  # 地図はサイト（website.py）で書き出す
      self.run_main(["--map"])

  def test_map_grid_writes_a_file_without_advancing(self):
    self.new_game("アリス")
    out = self.dir / "grid.svg"
    self.assertEqual(self.run_main(["--map-grid", str(out)]), 0)
    self.assertIn("kyoto (784, 641)", out.read_text(encoding="utf-8"))  # 確認用: 場所の ID と座標
    self.assertEqual(load("h1")["day"], 0)

  def test_no_gist_advances_development_without_touching_the_production_board(self):
    self.new_game("アリス")
    self.assertEqual(self.daily("2026-12-01"), 0)
    board_before = rpg.BOARD_PATH.read_bytes()
    self.create_board.reset_mock()

    self.assertEqual(self.run_main(["--date", "2026-12-02", "--no-gist"]), 0)
    self.assertEqual(load("h1")["last_date"], "2026-12-02")
    self.assertEqual(json.loads(store.GUILD_PATH.read_text())["refreshed"][load("h1")["place"]], "2026-12-02")
    self.assertEqual(rpg.BOARD_PATH.read_bytes(), board_before)
    self.create_board.assert_not_called()
    self.update_gist.assert_not_called()

  def test_dry_run_writes_nothing(self):
    self.new_game("アリス")
    self.run_main(["--dry-run", "--actions", "1"])
    self.create_board.assert_not_called()
    self.update_gist.assert_not_called()
    self.assertEqual(load("h1")["day"], 0)
    self.assertIsNone(rpg.load_board())


if __name__ == "__main__":
  unittest.main()
