"""Durable Antigravity transcript event cursor, independent of display/routing text."""
import hashlib
import json
import os


class TranscriptCursor:
    def __init__(self, state_file):
        self.state_file = state_file
        try:
            with open(state_file, encoding="utf-8") as f:
                self.paths = json.load(f)
        except FileNotFoundError:
            self.paths = {}

    def _save(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.paths, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_file)

    def pending(self, path):
        path = os.path.normcase(os.path.abspath(path))
        events = {}
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if (row.get("source") != "MODEL" or row.get("type") != "PLANNER_RESPONSE"
                        or row.get("status") != "DONE" or not row.get("content")
                        or row.get("tool_calls")):
                    continue
                text = row["content"].strip()
                identity = json.dumps([row.get("step_index"), text], ensure_ascii=False)
                key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                events[key] = text
        if path not in self.paths:
            # First attachment establishes a baseline, never broadcasts old history.
            self.paths[path] = list(events)
            self._save()
            return []
        seen = set(self.paths[path])
        return [(key, text) for key, text in events.items() if key not in seen]

    def commit(self, path, event_id):
        path = os.path.normcase(os.path.abspath(path))
        if event_id not in self.paths[path]:
            self.paths[path].append(event_id)
            self._save()
