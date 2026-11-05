"""ネタ（Pin に載せる内容）のスクリプトで共通に使う部品（GitHub API・Gist の更新・表示幅）。"""
from __future__ import annotations

import json
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
RETRY_WAITS = (2, 5)  # 一時的なエラーの再試行までの待ち秒数
# Pinned カードは 1 行 55 桁前後で切れる。行（見出し以外）はこの桁数に収める
LINE_MAX = 49


class FetchError(Exception):
    """再試行してもデータを取得できなかった。"""


def warn(msg: str) -> None:
    """GitHub Actions のサマリーに警告として表示する（ローカルではただの出力）。"""
    print(f"::warning::{msg}", file=sys.stderr)


def api(method: str, url: str, token: str | None, body: dict | None = None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gistrap",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def width(s: str) -> int:
    """全角文字を 2 として数えた表示幅。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def bar(value: int, top: int, bar_w: int) -> str:
    """top を bar_w とした横棒。フォント差でずれにくいよう █ だけを使い、value が 1 以上なら最低 1 つは出す。"""
    n = max(1, round(bar_w * value / top)) if top and value > 0 else 0
    return "█" * n + " " * (bar_w - n)


def create_gist(token: str, filename: str, content: str, description: str) -> dict:
    """public Gist を 1 ファイルで作り、API のレスポンス（id・html_url など）を返す。"""
    body = {"public": True, "description": description, "files": {filename: {"content": content}}}
    try:
        return api("POST", "https://api.github.com/gists", token, body)
    except urllib.error.HTTPError as e:
        hint = "（Classic PAT の gist スコープと期限を確認してください）" if e.code in (401, 403, 404) else ""
        raise SystemExit(f"Gist API エラー {e.code}{hint}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:  # POST は冪等でないので再試行しない
        raise SystemExit(f"Gist API に接続できません: {e.reason}")


def update_gist(gist_id: str, token: str, filename: str, content: str) -> None:
    try:
        files = api("GET", f"https://api.github.com/gists/{gist_id}", token).get("files", {})
        if len(files) > 1 and min(files) != filename:
            # Pinned カードには名前順で先頭のファイルが出る
            warn(f"Gist {gist_id} には複数のファイルがあり、Pin には {min(files)} が表示されます")
        if files.get(filename, {}).get("content") == content:
            print(f"変更なし: {filename} の更新をスキップ")
            return
        if filename not in files and len(files) == 1:
            # 作成時の仮ファイル名を置き換える（Pinned には名前順で先頭のファイルが出るため）
            (old,) = files
            patch = {old: {"filename": filename, "content": content}}
        else:
            patch = {filename: {"content": content}}
        api("PATCH", f"https://api.github.com/gists/{gist_id}", token, {"files": patch})
    except urllib.error.HTTPError as e:
        hint = "（Classic PAT の gist スコープ・期限と Gist ID を確認してください）" if e.code in (401, 403, 404) else ""
        raise SystemExit(f"Gist API エラー {e.code}{hint}: {e.read().decode(errors='replace')}")
    print(f"Gist を更新しました: {filename}")
