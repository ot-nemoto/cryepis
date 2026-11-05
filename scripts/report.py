#!/usr/bin/env python3
"""ハニーポットの集計 JSON を読み、Pinned 用のカード（最大 6 枚）を作って Gist を更新する。

集計 JSON は VM 側（honeypot/aggregate.py）が作り、Object Storage に置く。
ここでは読み取り専用の事前認証 URL（HONEYPOT_DATA_URL）から取得するだけで、生のログや IP には触れない。
形式は docs/DATA_FORMAT.md を参照。

環境変数:
  HONEYPOT_DATA_URL  集計 JSON の URL（読み取り専用の事前認証 URL）。--sample 指定時は不要
  GIST_PAT           Gist の作成・更新に使う gist スコープの Classic PAT
  GIST_IDS           カードごとの Gist ID を入れた JSON（例 {"overview":"abc","creds":"def"}）
                     登録済みのカードだけ更新する（未設定なら表示だけ）

使い方:
  python scripts/report.py --sample tests/sample_data.json --dry-run  # 記録せずにカードを表示
  GIST_PAT=xxx python scripts/report.py --create-gists                 # 6 枚の Gist をまとめて作る
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402

GIST_DESCRIPTION = "gistrap — ハニーポットに来たスキャン・総当たりの集計"
# カードの定義（キー: 見出しと Gist のファイル名）。Pinned には名前順で先頭のファイルが出るので番号を付ける
CARDS = ["overview", "creds", "commands", "http", "ports", "geo"]
FILENAME = {k: f"honeypot-{i}-{k}.txt" for i, k in enumerate(CARDS, 1)}
TITLE = {
    "overview": "🍯 Honeypot",
    "creds": "🔑 試されたパスワード",
    "commands": "⌨️ 侵入後のコマンド",
    "http": "🌐 狙われた URL",
    "ports": "🚪 叩かれたポート",
    "geo": "🌍 攻撃元の国",
}
# 伏せ字にする語（下品・差別的な語の最小限の例。VM 側でも落とすが、表示前にも念のため）
BAD = re.compile(r"(fuck|shit|bitch|nigger|cunt)", re.I)


def mask(value: str, limit: int = 40) -> str:
    """表示前の防御。制御文字・長すぎる文字列・NG 語を伏せる。"""
    value = "".join(c for c in str(value) if c.isprintable())
    if BAD.search(value):
        return "***"
    return value[:limit]


def fmt(n: int) -> str:
    return f"{n:,}"


def clip(text: str, w: int) -> str:
    """表示幅が w を超えたら末尾を「…」にして詰める。"""
    if common.width(text) <= w:
        return text
    while common.width(text) > w - 1:
        text = text[:-1]
    return text + "…"


def pad(text: str, w: int) -> str:
    return text + " " * (w - common.width(text))


def rank_lines(items: list[dict], n: int = 4, bar_w: int = 10) -> list[str]:
    """「順位. 値 棒 件数」の行を最大 n 本作る。各行は LINE_MAX 桁に収める。"""
    items = items[:n]
    if not items:
        return ["（まだ記録がありません）"]
    top = max(it["n"] for it in items)
    numw = max(len(fmt(it["n"])) for it in items)
    lab_w = max(1, common.LINE_MAX - (5 + bar_w + numw))  # "N. " + 空白2 + 棒 + 件数
    lines = []
    for i, it in enumerate(items, 1):
        label = pad(clip(mask(it["v"]), lab_w), lab_w)
        lines.append(f"{i}. {label} {common.bar(it['n'], top, bar_w)} {fmt(it['n']):>{numw}}")
    return lines


def mmdd(date: str) -> str:
    return f"{date[5:7]}/{date[8:10]}" if len(date) >= 10 else date


def build_card(key: str, data: dict) -> str:
    """1 枚分のテキスト（見出し + 本文）を作る。"""
    t = data.get("totals", {})
    header = f"{TITLE[key]} {mmdd(data.get('date', ''))}"
    if key == "overview":
        body = [
            f"📊 {fmt(t.get('sessions', 0))} 接続 / {fmt(t.get('unique_ips', 0))} IP / {fmt(t.get('countries', 0))} ヶ国",
            f"🔑 ログイン試行 {fmt(t.get('login_attempts', 0))}",
            f"🌐 HTTP {fmt(t.get('http_requests', 0))} / 🚪 ポート {fmt(t.get('port_hits', 0))}",
            f"⬇️ DL 試行 {fmt(t.get('downloads_attempted', 0))}",
        ]
    elif key == "creds":
        body = rank_lines(data.get("top_passwords", []))
    elif key == "commands":
        body = rank_lines(data.get("top_commands", []))
    elif key == "http":
        body = rank_lines(data.get("top_http_paths", []))
    elif key == "ports":
        items = [{"v": f"{it['v']}/{it.get('proto', 'tcp')}", "n": it["n"]} for it in data.get("top_ports", [])]
        body = rank_lines(items)
    else:  # geo
        body = rank_lines(data.get("top_countries", []))
    gen = data.get("generated_at", "")
    footer = ["", f"更新 {gen[:16].replace('T', ' ')} UTC" if gen else "", "github.com/ot-nemoto/gistrap"]
    return "\n".join([header, *body, *footer]).rstrip() + "\n"


def empty_data() -> dict:
    return {"date": datetime.now(common.JST).date().isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "totals": {}}


def load_data(sample: str | None) -> dict:
    if sample:
        with open(sample, encoding="utf-8") as f:
            return json.load(f)
    url = os.environ.get("HONEYPOT_DATA_URL")
    if not url:
        raise SystemExit("HONEYPOT_DATA_URL か --sample を指定してください")
    try:
        with __import__("urllib.request", fromlist=["request"]).urlopen(url, timeout=20) as res:
            return json.load(res)
    except OSError as e:
        raise common.FetchError(f"集計データを取得できません: {e}")


def gist_ids() -> dict:
    raw = os.environ.get("GIST_IDS", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit("GIST_IDS が JSON として読めません（例 {\"overview\":\"abc\"}）")


def create_gists() -> int:
    token = os.environ.get("GIST_PAT")
    if not token:
        raise SystemExit("GIST_PAT を環境変数で指定してください")
    if gist_ids():
        raise SystemExit("GIST_IDS が設定済みのため、Gist は作りません")
    data = empty_data()
    ids = {}
    for key in CARDS:
        gist = common.create_gist(token, FILENAME[key], build_card(key, data), f"{GIST_DESCRIPTION}（{key}）")
        ids[key] = gist["id"]
        print(f"{key}: {gist['html_url']}")
    blob = json.dumps(ids, ensure_ascii=False)
    message = f"6 枚の Gist を作りました。Repository variable `GIST_IDS` に次を登録してください:\n\n    {blob}\n"
    print("\n" + message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## gistrap の Gist\n\n```\n{blob}\n```\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Gist を更新せずカードを表示する")
    ap.add_argument("--sample", help="集計 JSON のファイル（URL の代わり。動作確認用）")
    ap.add_argument("--create-gists", action="store_true", help="6 枚の Gist をまとめて作る")
    a = ap.parse_args()
    if a.create_gists:
        return create_gists()

    data = load_data(a.sample)
    cards = {key: build_card(key, data) for key in CARDS}
    for key in CARDS:
        print(f"===== {key} ({FILENAME[key]}) =====\n{cards[key]}")
    if a.dry_run:
        return 0
    ids = gist_ids()
    if not ids:
        print("GIST_IDS が未設定のため Gist の更新をスキップ")
        return 0
    token = os.environ.get("GIST_PAT")
    if not token:
        raise SystemExit("GIST_PAT を環境変数で指定してください")
    for key in CARDS:
        if ids.get(key):
            common.update_gist(ids[key], token, FILENAME[key], cards[key])
    return 0


if __name__ == "__main__":
    sys.exit(main())
