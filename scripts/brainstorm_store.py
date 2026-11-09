import json, os, threading, uuid, time

class BrainstormStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f: return json.load(f)
        except FileNotFoundError: return {"questions": [], "turns": {}}
    def save(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + "." + uuid.uuid4().hex + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, self.path)
    def question(self, msg):
        agents = list(dict.fromkeys(x for x in msg.get("to", []) if x in ("codex","ag","dsh","mimo","arch")))
        if msg.get("sender") != "human" or msg.get("kind") != "text" or len(agents) < 2: return
        with self.lock:
            data = self.load()
            if any(q["message_id"] == msg["id"] for q in data["questions"]): return
            data["questions"].append({"id": "Q%d" % (len(data["questions"])+1), "message_id": msg["id"], "text": msg["text"], "ts": msg["ts"], "status": "collecting", "answers": {a: [] for a in agents}})
            self.save(data)
    def bind(self, turn, agent, mids):
        with self.lock:
            data = self.load(); ids = [q["id"] for q in data["questions"] if q["message_id"] in mids and agent in q["answers"]]
            data["turns"][turn] = {"agent": agent, "questions": ids}; self.save(data)
    def answer(self, turn, msg):
        if not turn or msg.get("kind") != "text" or "human" not in msg.get("to", []): return
        if (msg.get("text") or "").strip() in ("[[silent]]", "[[skip]]", "[[静默]]"): return
        with self.lock:
            data = self.load(); route = data["turns"].get(turn)
            if not route or route["agent"] != msg["sender"]: return
            # A batched answer to several questions is ambiguous; do not guess.
            if len(route["questions"]) != 1: return
            for q in data["questions"]:
                if q["id"] in route["questions"]:
                    slot = q["answers"][msg["sender"]]
                    if not any(a["message_id"] == msg["id"] for a in slot):
                        slot.append({"message_id": msg["id"], "text": msg["text"], "ts": msg["ts"]})
                    q["status"] = "complete" if all(q["answers"].values()) else "collecting"
            self.save(data)
    def page(self, offset=0, limit=20):
        with self.lock:
            qs = self.load()["questions"]
            return {"questions": list(reversed(qs))[offset:offset+limit], "total": len(qs), "offset": offset, "limit": limit}
