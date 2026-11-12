"""Read local Codex metadata and lifecycle logs without modifying Codex files."""
import json
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

START = {"task_started", "turn_started"}
END = {"task_complete", "turn_complete", "turn_aborted", "task_failed", "turn_failed"}


def seconds(value, fallback=None):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10**11 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def reverse_lines(path):
    """Yield complete JSONL records newest first, excluding an unfinished write."""
    with path.open("rb") as stream:
        position = stream.seek(0, 2)
        remainder = b""
        first = True
        while position:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            parts = (stream.read(size) + remainder).split(b"\n")
            remainder = parts.pop(0)
            if first:
                parts.pop()  # Empty after newline, or a partial record still being written.
                first = False
            yield from reversed(parts)
        if remainder:
            yield remainder


def latest_run(path):
    terminal = None
    for line in reverse_lines(path):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if record.get("type") != "event_msg":
            continue
        event = record.get("payload", {})
        kind = event.get("type")
        if kind not in START | END:
            continue
        at = seconds(record.get("timestamp"), time.time())
        if kind in END:
            if terminal is not None:
                continue
            status = "completed" if kind in {"task_complete", "turn_complete"} else (
                "interrupted" if kind == "turn_aborted" else "failed")
            # Some versions carry the final status in the payload.
            if event.get("status") in {"failed", "interrupted"}:
                status = event["status"]
            terminal = {"turnId": event.get("turn_id", str(at)), "status": status,
                        "startedAt": seconds(event.get("started_at")),
                        "endedAt": seconds(event.get("completed_at"), at),
                        "duration": event.get("duration_ms")}
            if terminal["startedAt"] is not None:
                break
        elif terminal is None:
            return {"turnId": event.get("turn_id", str(at)), "status": "running",
                    "startedAt": seconds(event.get("started_at"), at), "endedAt": None}
        elif event.get("turn_id") == terminal["turnId"]:
            terminal["startedAt"] = seconds(event.get("started_at"), at)
            break
    if terminal:
        duration = terminal.pop("duration", None)
        if terminal["startedAt"] is None and isinstance(duration, (int, float)):
            terminal["startedAt"] = terminal["endedAt"] - duration / 1000
    return terminal


class CodexSource:
    def __init__(self, home, state_path=None):
        self.home = Path(home).expanduser().resolve()
        self.cache = {}
        self.observed = set()
        self.started = time.time()
        self.day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        self.state_path = Path(state_path) if state_path else None
        self.retained = {}
        if self.state_path:
            try:
                self.retained = {t["id"]: t for t in json.loads(self.state_path.read_text())
                                 if t.get("status") in {"completed", "interrupted", "failed"}}
                self.observed.update(self.retained)
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def live_writers(self):
        locks = list((self.home / "thread-writer-locks").glob("*.lock"))
        if not locks:
            return set()
        try:
            result = subprocess.run(["/usr/sbin/lsof", "-n", "-Fn", *map(str, locks)],
                                    capture_output=True, text=True, timeout=4)
            if result.returncode not in (0, 1):
                return None
            return {Path(line[1:]).stem for line in result.stdout.splitlines()
                    if line.startswith("n") and line.endswith(".lock")}
        except (OSError, subprocess.TimeoutExpired):
            return None

    def snapshot(self):
        database = self.home / "state_5.sqlite"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, rollout_path, cwd, name, title FROM threads").fetchall()
        live = self.live_writers()
        tasks = []
        unavailable = 0
        for row in rows:
            path = Path(row["rollout_path"])
            try:
                stat = path.stat()
                signature = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
                cached = self.cache.get(row["id"])
                if not cached or cached[0] != signature:
                    cached = (signature, latest_run(path))
                    self.cache[row["id"]] = cached
                run = cached[1]
            except OSError:
                unavailable += 1
                continue
            if not run:
                continue
            task = dict(run)
            task.update(id=row["id"], title=(row["name"] or row["title"] or "未命名任务")[:240],
                        project=Path(row["cwd"]).name or "Codex", lastActivityAt=stat.st_mtime)
            if task["status"] == "running":
                if live is None or row["id"] not in live:
                    # A missing completion event is never evidence of success.
                    task["status"] = "unknown"
                    if stat.st_mtime < self.day_start and row["id"] not in self.observed:
                        continue
                else:
                    self.observed.add(row["id"])
            elif task["endedAt"] < self.day_start and row["id"] not in self.observed:
                continue
            tasks.append(task)
        current = {t["id"]: t for t in tasks}
        saved = dict(self.retained)
        for task in tasks:
            if task["status"] in {"completed", "interrupted", "failed"}:
                self.retained[task["id"]] = task
            else:
                self.retained.pop(task["id"], None)
        if self.state_path and saved != self.retained:
            self.state_path.parent.mkdir(exist_ok=True, parents=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(list(self.retained.values()), ensure_ascii=False))
            temporary.replace(self.state_path)
        tasks = list({**self.retained, **current}.values())
        tasks.sort(key=lambda t: (t["status"] != "running", -(t["endedAt"] or t["startedAt"] or 0)))
        return {"tasks": tasks, "serverTime": time.time(), "connected": True,
                "source": "local-logs", "scannedThreads": len(rows),
                "unavailableThreads": unavailable, "writerCheckAvailable": live is not None,
                "startedAt": self.started}
