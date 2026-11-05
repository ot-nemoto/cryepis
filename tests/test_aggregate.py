import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "honeypot"))
import aggregate  # noqa: E402


class RedactTest(unittest.TestCase):
    def test_strips_control_masks_bad_and_clips(self):
        self.assertEqual(aggregate.redact("a\x00b"), "ab")
        self.assertEqual(aggregate.redact("SHIT"), "***")
        self.assertEqual(aggregate.redact("x" * 100), "x" * aggregate.MAX_LEN)


class BuildReportTest(unittest.TestCase):
    def geo(self, ip):
        return {"US": {"country": "US", "network": "AS1 Example"}}.get(ip, {})

    def test_counts_events_without_leaking_ips(self):
        cowrie = [
            {"eventid": "cowrie.session.connect", "src_ip": "US"},
            {"eventid": "cowrie.login.failed", "username": "root", "password": "123456"},
            {"eventid": "cowrie.login.failed", "username": "root", "password": "admin"},
            {"eventid": "cowrie.command.input", "input": "uname -a"},
            {"eventid": "cowrie.session.file_download", "url": "http://x/y"},
        ]
        http = [{"src_ip": "US", "path": "/.env"}, {"src_ip": "1.2.3.4", "path": "/.env"}]
        ports = [{"src_ip": "US", "proto": "tcp", "port": 22}, {"src_ip": "US", "proto": "tcp", "port": 22}]
        rep = aggregate.build_report("2026-11-05", cowrie, http, ports, self.geo)

        self.assertEqual(rep["totals"]["sessions"], 1)
        self.assertEqual(rep["totals"]["login_attempts"], 2)
        self.assertEqual(rep["totals"]["http_requests"], 2)
        self.assertEqual(rep["totals"]["port_hits"], 2)
        self.assertEqual(rep["totals"]["downloads_attempted"], 1)
        self.assertEqual(rep["totals"]["unique_ips"], 2)  # "US" と "1.2.3.4"
        self.assertEqual(rep["top_usernames"][0], {"v": "root", "n": 2})
        self.assertEqual(rep["top_http_paths"][0], {"v": "/.env", "n": 2})
        self.assertEqual(rep["top_ports"][0], {"v": 22, "proto": "tcp", "n": 2})
        self.assertEqual(rep["top_countries"][0], {"v": "US", "n": 4})  # connect1 + http1 + ports2
        serialized = str(rep)
        self.assertNotIn("1.2.3.4", serialized)  # 生の IP は出さない

    def test_iptables_parsing(self):
        lines = [
            "Nov  5 00:00:01 vm kernel: HONEYPOT IN=eth0 SRC=203.0.113.9 DST=10.0.0.2 PROTO=TCP SPT=5 DPT=23 ",
            "Nov  5 00:00:02 vm kernel: other log line PROTO=TCP DPT=80",
        ]
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("\n".join(lines))
            path = f.name
        rows = aggregate.read_iptables(path)
        self.assertEqual(rows, [{"src_ip": "203.0.113.9", "proto": "tcp", "port": 23}])


if __name__ == "__main__":
    unittest.main()
