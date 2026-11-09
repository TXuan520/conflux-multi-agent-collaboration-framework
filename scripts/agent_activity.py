"""Per-run activity records shared by Hub and external workers."""
import confluxconfig as cfg
import contextlib
import functools
import json
import os
from pathlib import Path
import threading
import time
import uuid

DIRECTORY = Path(cfg.root_path('data/hub/agent-activity'))

@contextlib.contextmanager
def reporting(agent, task=""):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DIRECTORY / (agent + "-" + uuid.uuid4().hex + ".json")
    stop = threading.Event()
    record = dict(agent=agent, pid=os.getpid(), started=time.time(), task=str(task)[:120])
    def emit(state):
        try:
            data = dict(record, state=state, updated=time.time())
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, path)
        except OSError:
            pass
    def pulse():
        while not stop.wait(5):
            emit('running')
    emit('running')
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        yield record
    finally:
        stop.set()
        thread.join(timeout=6)
        path.unlink(missing_ok=True)
        path.with_suffix('.tmp').unlink(missing_ok=True)

def reported(agent):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(self, *args, **kwargs):
            name = getattr(self, 'provider', agent) if agent == 'provider' else agent
            task = getattr(self, 'task', '') if agent == 'provider' else (args[0] if args else kwargs.get('text', ''))
            with reporting(name, task) as record:
                self._activity_record = record
                try:
                    return fn(self, *args, **kwargs)
                finally:
                    self._activity_record = None
        return wrapped
    return decorate
