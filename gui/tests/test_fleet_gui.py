"""Tests for the fleet GUI. Run from the repo root:

    python3 -m unittest discover -s gui/tests
"""

import json
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fleet_gui import poller  # pylint: disable=wrong-import-position
from fleet_gui import sources  # pylint: disable=wrong-import-position
from fleet_gui import state  # pylint: disable=wrong-import-position
from fleet_gui import web  # pylint: disable=wrong-import-position


class ParseHostsConfTest(unittest.TestCase):

    def test_parses_names_aliases_and_hub(self):
        text = """
            # name   ssh-alias  gpus
            hub      local      0
            box-a    box-a      0,1   # two cards
            """
        self.assertEqual(
            sources.parse_hosts_conf(text, hub_alias="rig-hub"),
            [state.Host("hub", "rig-hub", is_hub=True),
             state.Host("box-a", "box-a", is_hub=False)])

    def test_skips_aliases_that_look_like_ssh_options(self):
        text = "evil  -oProxyCommand=touch_x\nok  user@ok-host\n"
        self.assertEqual(
            sources.parse_hosts_conf(text, hub_alias="hub"),
            [state.Host("ok", "user@ok-host")])


class FleetStateTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.host = state.Host("box", "box")
        self.snapshot = {"gpus": [{"index": 0, "util": 50}], "logs": [
            {"name": "rig-runs/a/stdout.log", "path": "/runs/a/stdout.log"}]}

    def test_failed_poll_keeps_last_snapshot_flagged_offline(self):
        fleet = state.FleetState(poll_interval_s=20)
        fleet.record_poll(self.host, self.snapshot, fetched=100)
        fleet.record_poll(self.host, None, error="timeout", fetched=120)

        machine = fleet.to_json()["rigs"]["box"]
        self.assertFalse(machine["online"])
        self.assertEqual(machine["error"], "timeout")
        self.assertEqual(machine["snapshot"], self.snapshot)
        self.assertEqual(machine["last_online"], 100)

    def test_history_is_capped(self):
        fleet = state.FleetState(poll_interval_s=20, history_len=3)
        for t in range(5):
            fleet.add_sample("box:0", t, t * 10)
        self.assertEqual(fleet.to_json()["hist"]["box:0"],
                         [[2, 20], [3, 30], [4, 40]])

    def test_log_target_lists_only_collector_paths(self):
        fleet = state.FleetState(poll_interval_s=20)
        fleet.record_poll(self.host, self.snapshot)
        host, allowed = fleet.log_target("box")
        self.assertEqual(host, self.host)
        self.assertEqual(allowed, {"/runs/a/stdout.log"})
        self.assertEqual(fleet.log_target("nope"), (None, frozenset()))


class WebTest(unittest.TestCase):
    """End-to-end over HTTP against the simulated fleet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fleet = state.FleetState(poll_interval_s=5, demo=True)
        cls.source = sources.DemoFleet(seed=1)
        poller.poll_once(cls.source, cls.fleet)
        fleet_poller = poller.Poller(cls.source, cls.fleet, interval_s=3600)
        cls.server = web.make_server(0, cls.fleet, cls.source, fleet_poller)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def fetch(self, path, method="GET"):
        request = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_serves_static_files(self):
        for path in ("/", "/app.js", "/theme.js", "/styles.css"):
            with self.subTest(path=path):
                status, body = self.fetch(path)
                self.assertEqual(status, 200)
                self.assertTrue(body)

    def test_state_lists_every_demo_machine(self):
        status, body = self.fetch("/api/state")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["demo"])
        self.assertEqual(data["hosts"], ["hub", "gpu-box-a", "gpu-box-b"])
        self.assertEqual(len(data["rigs"]["gpu-box-a"]["snapshot"]["gpus"]), 2)

    def test_log_tails_listed_path(self):
        status, body = self.fetch(
            "/api/log?rig=hub&path=/demo/train-vit-s&n=20")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["text"].splitlines()), 20)

    def test_log_refuses_unlisted_path(self):
        status, _ = self.fetch("/api/log?rig=hub&path=/etc/passwd")
        self.assertEqual(status, 404)

    def test_log_rejects_bad_line_count(self):
        status, _ = self.fetch("/api/log?rig=hub&path=/demo/train-vit-s&n=x")
        self.assertEqual(status, 400)

    def test_refresh_requires_post(self):
        self.assertEqual(self.fetch("/api/refresh")[0], 404)
        self.assertEqual(self.fetch("/api/refresh", method="POST")[0], 202)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.fetch("/../server.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
