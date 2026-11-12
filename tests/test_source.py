import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.source import CodexSource, latest_run


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / "run.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def event(self, kind, **kwargs):
        with self.path.open("a") as f:
            f.write(json.dumps({"type": "event_msg", "timestamp": "2026-11-12T05:00:00Z",
                                "payload": {"type": kind, "turn_id": "turn1", **kwargs}}) + "\n")

    def test_real_started_at_and_completion_duration(self):
        self.event("task_started", started_at=100)
        self.assertEqual(latest_run(self.path)["status"], "running")
        self.event("task_complete", started_at=100, completed_at=150, duration_ms=50000)
        run = latest_run(self.path)
        self.assertEqual((run["status"], run["startedAt"], run["endedAt"]), ("completed", 100, 150))

    def test_interruption_is_not_success(self):
        self.event("task_started", started_at=100)
        self.event("turn_aborted")
        self.assertEqual(latest_run(self.path)["status"], "interrupted")

    def test_failed_is_not_success(self):
        self.event("task_complete", status="failed", started_at=100)
        self.assertEqual(latest_run(self.path)["status"], "failed")

    def test_latest_turn_replaces_completed_turn(self):
        self.event("task_complete", started_at=100)
        self.event("task_started", turn_id="turn2", started_at=200)
        self.assertEqual(latest_run(self.path)["turnId"], "turn2")

    def test_partial_write_is_ignored_until_newline(self):
        self.event("task_started", started_at=100)
        with self.path.open("a") as f:
            f.write('{"type":"event_msg","payload":{"type":"task_complete"}}')
        self.assertEqual(latest_run(self.path)["status"], "running")
        with self.path.open("a") as f:
            f.write("\n")
        self.assertEqual(latest_run(self.path)["status"], "completed")

    def test_large_non_lifecycle_record_is_skipped(self):
        self.event("task_started", started_at=100)
        self.event("agent_message", message="a" * 180000)
        self.assertEqual(latest_run(self.path)["startedAt"], 100)

    def test_missing_end_without_writer_is_unknown(self):
        self.event("task_started", started_at=time.time())
        c = sqlite3.connect(self.home / "state_5.sqlite")
        c.execute("create table threads(id,rollout_path,cwd,name,title)")
        c.execute("insert into threads values(?,?,?,?,?)", ("thread1", str(self.path), "/project", "测试任务", "private body"))
        c.commit(); c.close()
        source = CodexSource(self.home)
        with patch.object(source, "live_writers", return_value=set()):
            data = source.snapshot()
        self.assertEqual(data["tasks"][0]["status"], "unknown")
        self.assertNotIn("private body", json.dumps(data))
        with patch.object(source, "live_writers", return_value={"thread1"}):
            self.assertEqual(source.snapshot()["tasks"][0]["status"], "running")
        self.event("task_complete", started_at=time.time()-10, completed_at=time.time())
        with patch.object(source, "live_writers", return_value=set()):
            self.assertEqual(source.snapshot()["tasks"][0]["status"], "completed")

    def test_completed_record_survives_restart(self):
        now = time.time()
        self.event("task_complete", started_at=now-60, completed_at=now)
        c = sqlite3.connect(self.home / "state_5.sqlite")
        c.execute("create table threads(id,rollout_path,cwd,name,title)")
        c.execute("insert into threads values(?,?,?,?,?)", ("thread1", str(self.path), "/project", "完成任务", ""))
        c.commit(); c.close()
        state_path = self.home / "monitor-state.json"
        source = CodexSource(self.home, state_path)
        source.snapshot()
        restarted = CodexSource(self.home, state_path)
        restarted.day_start = now + 86400
        self.assertEqual(restarted.snapshot()["tasks"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
