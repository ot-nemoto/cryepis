#!/usr/bin/env python3
"""VM 側で 1 日分のログを集計し、IP を含まない JSON を作る（必要なら Object Storage へ送る）。

入力:
  - Cowrie の JSON ログ（SSH/Telnet。1 行 1 イベント）
  - HTTP の偽サーバーのログ（http_server.py。1 行 1 リクエスト）
  - iptables LOG（全ポートへの接続の試み。/var/log/kern.log 形式）

出力の形式は docs/DATA_FORMAT.md を参照。生のログ・IP はこのスクリプトの外へ出さない。
集計には国・ネットワーク名だけを残し、値は短く詰め、表示に向かない語は伏せる。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone

TOP_N = 10
MAX_LEN = 60  # 値の最大の長さ（長いものは切り詰める）
BAD = re.compile(r"(fuck|shit|bitch|nigger|cunt)", re.I)


def redact(value: str) -> str:
    """表示に向かない語を伏せ、制御文字を除き、長すぎるものを詰める。"""
    value = "".join(c for c in str(value) if c.isprintable()).strip()
    if BAD.search(value):
        return "***"
    return value[:MAX_LEN] if len(value) > MAX_LEN else value


def top(counter: Counter, n: int = TOP_N) -> list[dict]:
    return [{"v": v, "n": c} for v, c in counter.most_common(n)]


def build_report(date: str, cowrie: list[dict], http: list[dict], ports: list[dict], geo=None) -> dict:
    """各ログのイベント列から集計 JSON を作る。geo は ip -> {"country","network"} を返す関数（任意）。"""
    ips: set[str] = set()
    countries: Counter = Counter()
    networks: Counter = Counter()
    usernames: Counter = Counter()
    passwords: Counter = Counter()
    commands: Counter = Counter()
    http_paths: Counter = Counter()
    port_hits: Counter = Counter()
    sessions = login_attempts = downloads = 0

    def note_ip(ip: str | None) -> None:
        if not ip:
            return
        ips.add(ip)
        if geo:
            g = geo(ip) or {}
            if g.get("country"):
                countries[g["country"]] += 1
            if g.get("network"):
                networks[g["network"]] += 1

    for e in cowrie:
        eid = e.get("eventid", "")
        if eid == "cowrie.session.connect":
            sessions += 1
            note_ip(e.get("src_ip"))
        elif eid in ("cowrie.login.failed", "cowrie.login.success"):
            login_attempts += 1
            usernames[redact(e.get("username", ""))] += 1
            passwords[redact(e.get("password", ""))] += 1
        elif eid == "cowrie.command.input":
            commands[redact(e.get("input", ""))] += 1
        elif eid == "cowrie.session.file_download":
            downloads += 1

    for e in http:
        note_ip(e.get("src_ip"))
        http_paths[redact(e.get("path", "/"))] += 1

    for e in ports:
        port_hits[(int(e["port"]), e.get("proto", "tcp"))] += 1
        note_ip(e.get("src_ip"))

    return {
        "date": date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "sessions": sessions,
            "unique_ips": len(ips),
            "countries": len(countries),
            "login_attempts": login_attempts,
            "http_requests": len(http),
            "port_hits": sum(port_hits.values()),
            "downloads_attempted": downloads,
        },
        "top_usernames": top(usernames),
        "top_passwords": top(passwords),
        "top_commands": top(commands),
        "top_http_paths": top(http_paths),
        "top_ports": [{"v": p, "proto": proto, "n": n} for (p, proto), n in port_hits.most_common(TOP_N)],
        "top_countries": top(countries),
        "top_networks": top(networks),
    }


def read_json_lines(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return out


IPTABLES = re.compile(r"SRC=(?P<ip>[0-9.]+).*?PROTO=(?P<proto>TCP|UDP).*?DPT=(?P<port>\d+)")


def read_iptables(path: str, tag: str = "HONEYPOT") -> list[dict]:
    """iptables LOG（--log-prefix "HONEYPOT "）の行からポートへの接続の試みを拾う。"""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if tag not in line:
                    continue
                m = IPTABLES.search(line)
                if m:
                    out.append({"src_ip": m["ip"], "proto": m["proto"].lower(), "port": int(m["port"])})
    except FileNotFoundError:
        pass
    return out


def load_geo():
    """DB-IP Lite（任意）。無ければ国・ネットワークは空になる。"""
    try:
        import geoip2.database  # type: ignore
    except ImportError:
        return None
    import os
    db = os.environ.get("GEO_DB", "/opt/honeypot/dbip.mmdb")
    if not os.path.exists(db):
        return None
    reader = geoip2.database.Reader(db)

    def lookup(ip: str):
        try:
            r = reader.city(ip)
            net = r.traits.autonomous_system_organization or ""
            asn = r.traits.autonomous_system_number
            return {"country": r.country.iso_code or "", "network": (f"AS{asn} {net}".strip() if asn else net)}
        except Exception:  # noqa: BLE001  位置が引けない IP は無視
            return {}

    return lookup


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--cowrie", default="/opt/honeypot/cowrie/var/log/cowrie/cowrie.json")
    ap.add_argument("--http", default="/opt/honeypot/http/requests.log")
    ap.add_argument("--kern", default="/var/log/kern.log")
    ap.add_argument("--out", default="-", help="出力先のファイル（既定は標準出力）")
    a = ap.parse_args()
    report = build_report(a.date, read_json_lines(a.cowrie), read_json_lines(a.http),
                          read_iptables(a.kern), load_geo())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.out == "-":
        print(text)
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
