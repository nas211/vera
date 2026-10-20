"""Ordered, bounded paper execution queue, independent of the live feed reader."""
from concurrent.futures import Future
import queue
import threading
import time

from .log import get_logger

log = get_logger('paper_worker')


class PaperWorker:
    def __init__(self, paper, max_queue=10000):
        self.paper = paper
        self.queue = queue.Queue(maxsize=max_queue)
        self.failed = None
        self.last_processed_at = None
        self.last_lag_s = 0
        self.processing_started_at = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='paper-marks', daemon=True)
        self._thread.start()

    def _enqueue(self, method, args, future=None):
        if self.failed:
            if future:
                future.set_exception(RuntimeError('paper_worker_failed'))
            return
        try:
            self.queue.put_nowait((method, args, future, time.time()))
        except queue.Full:
            self.failed = 'paper_queue_overflow'
            log.error('paper queue overflow; paper processing halted')
            if future:
                future.set_exception(RuntimeError(self.failed))

    def on_mark(self, market, price, ts=None):
        self._enqueue('on_mark', (market, price, ts))

    def _call(self, method, *args):
        future = Future()
        self._enqueue(method, args, future)
        return future.result(timeout=30)

    def dispatch(self, signal):
        return self._call('dispatch', signal)

    def dispatch_close(self, signal):
        return self._call('dispatch_close', signal)

    def on_disconnect(self, reason='feed_disconnect'):
        self._enqueue('on_disconnect', (reason,))

    def status(self):
        with self.queue.mutex:
            oldest = self.queue.queue[0][3] if self.queue.queue else None
        outstanding = [t for t in (oldest, self.processing_started_at) if t is not None]
        age = max(0, time.time() - min(outstanding)) if outstanding else 0
        return dict(ready=not self.failed and self._thread.is_alive() and age <= 30,
                    oldest_event_age_s=age,
                    queued_events=self.queue.qsize(), last_error=self.failed,
                    last_processed_at=self.last_processed_at, processing_lag_s=self.last_lag_s)

    def shutdown(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self):
        pending = None
        while not self._stop.is_set():
            if self.failed:
                self.paper.on_disconnect(self.failed)
                # Resolve pending synchronous requests so callers fail visibly.
                while True:
                    try:
                        _, _, future, _ = self.queue.get_nowait()
                        if future:
                            future.set_exception(RuntimeError(self.failed))
                    except queue.Empty:
                        return
            try:
                item = pending or self.queue.get(timeout=.2)
                pending = None
            except queue.Empty:
                continue
            method, args, future, received = item
            self.processing_started_at = received
            try:
                if method == 'on_mark':
                    marks = [args]
                    # Small batching window avoids one remote transaction per tick.
                    self._stop.wait(.05)
                    while len(marks) < 500:
                        try:
                            next_item = self.queue.get_nowait()
                        except queue.Empty:
                            break
                        if next_item[0] != 'on_mark':
                            pending = next_item
                            break
                        marks.append(next_item[1])
                    self.paper.on_marks(marks)
                else:
                    result = getattr(self.paper, method)(*args)
                    if future:
                        future.set_result(result)
                self.last_processed_at = time.time()
                self.last_lag_s = max(0, self.last_processed_at - received)
                self.processing_started_at = None
            except Exception as exc:
                self.failed = type(exc).__name__
                log.error('paper worker halted: %s', self.failed)
                if future:
                    future.set_exception(RuntimeError('paper_processing_failed'))
                if pending and pending[2]:
                    pending[2].set_exception(RuntimeError('paper_processing_failed'))
                pending = None
