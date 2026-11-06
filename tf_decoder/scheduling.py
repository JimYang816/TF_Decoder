"""Serial analysis: newest foreground first, speculative work last."""
from concurrent.futures import Future
from queue import PriorityQueue
from threading import Thread
from itertools import count


class AnalysisExecutor:
    def __init__(self):
        self._queue = PriorityQueue()
        self._sequence = count()
        self._closed = False
        self._thread = Thread(target=self._run, name='raw-analysis', daemon=True)
        self._thread.start()

    def submit(self, function, *args, speculative=False):
        future = Future()
        if self._closed:
            future.set_exception(RuntimeError('应用已关闭'))
            return future
        number = next(self._sequence)
        self._queue.put(((1, number) if speculative else (0, -number), number, future, function, args))
        return future

    def _run(self):
        while True:
            _, _, future, function, args = self._queue.get()
            if function is None:
                return
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(function(*args))
                except BaseException as error:
                    future.set_exception(error)

    def shutdown(self, wait=True, cancel_futures=True):
        self._closed = True
        if cancel_futures:
            while not self._queue.empty():
                _, _, future, _, _ = self._queue.get_nowait()
                future.cancel()
        self._queue.put(((2, 0), next(self._sequence), Future(), None, ()))
        if wait:
            self._thread.join()
