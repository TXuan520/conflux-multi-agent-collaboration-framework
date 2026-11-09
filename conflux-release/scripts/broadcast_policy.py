"""Per-recipient 3-message / 60-second aggregation; no model IO."""
import threading


class BroadcastPolicy:
    PACK_SIZE = 3
    TIMEOUT = 60.0

    def __init__(self, deliver=None, timer_factory=None):
        self.lock = threading.RLock()
        self.deliver = deliver or (lambda *args: None)
        self.timer_factory = timer_factory or threading.Timer
        self.buckets = {}
        self.outboxes = {}
        self.draining = set()
        self.seen = {}
        self.epoch = 0
        self.generation = None
        self.broadcast_enabled = None

    def reset(self, generation=None, enabled=None):
        with self.lock:
            self.epoch += 1
            self.generation = generation
            self.broadcast_enabled = enabled
            for bucket in self.buckets.values():
                bucket['timer'].cancel()
            self.buckets.clear()
            self.outboxes.clear()
            self.seen.clear()

    def drop(self, target):
        with self.lock:
            bucket = self.buckets.pop(target, None)
            if bucket:
                bucket['timer'].cancel()
            self.outboxes.pop(target, None)
            self.seen.pop(target, None)

    def _take(self, target):
        bucket = self.buckets.pop(target, None)
        if bucket:
            bucket['timer'].cancel()
            return bucket['items']
        return []

    def _queue(self, target, items, generation, reason):
        if items:
            self.outboxes.setdefault(target, []).append((items, generation, reason, self.epoch))

    def _drain(self, target):
        with self.lock:
            if target in self.draining:
                return
            self.draining.add(target)
        try:
            while True:
                with self.lock:
                    box = self.outboxes.get(target)
                    if not box:
                        # Remove ownership atomically so a concurrent submit cannot strand data.
                        self.draining.discard(target)
                        return
                    items, generation, reason, epoch = box.pop(0)
                    if epoch != self.epoch:
                        continue
                self.deliver(target, items, generation, reason)
        except BaseException:
            with self.lock:
                self.draining.discard(target)
            raise

    def _expire(self, target, bucket):
        with self.lock:
            if self.buckets.get(target) is not bucket:
                return
            items = self._take(target)
            self._queue(target, items, bucket['generation'], 'peer_timeout')
        self._drain(target)

    def submit(self, target, messages, generation=None, immediate=False):
        with self.lock:
            if generation is not None:
                if self.generation is None:
                    self.generation = generation
                if generation != self.generation or self.broadcast_enabled is False:
                    return
            elif self.broadcast_enabled is True:
                return
            seen = self.seen.setdefault(target, set())
            for sender, text, mid in messages:
                if sender == target or (generation, mid) in seen:
                    continue
                seen.add((generation, mid))
                if len(seen) > 8192:
                    # IDs remain unique; retain a bounded replay ledger.
                    seen.clear()
                    seen.add((generation, mid))
                item = (sender, text, mid)
                if sender in ('human', 'system') or immediate:
                    tail = self._take(target) if sender == 'human' else []
                    self._queue(target, tail + [item], generation,
                                'human' if sender == 'human' else 'direct')
                    continue
                bucket = self.buckets.get(target)
                if bucket is None:
                    bucket = {'items': [], 'generation': generation}
                    timer = self.timer_factory(self.TIMEOUT, lambda t=target, b=bucket: self._expire(t, b))
                    timer.daemon = True
                    bucket['timer'] = timer
                    self.buckets[target] = bucket
                    bucket['items'].append(item)
                    timer.start()
                else:
                    bucket['items'].append(item)
                if len(bucket['items']) >= self.PACK_SIZE:
                    self._queue(target, self._take(target), generation, 'peer_full')
        self._drain(target)

    # Compatibility for older callers: autonomous silence never requires a retry.
    def must_reply(self, agent):
        return False

    def observe(self, agent, silent, event_id=None):
        return False

    def context(self, agent):
        return ''
