"""Map-building only: run ProcessPoolExecutor tasks in the calling process.

sqlglot's optimizer and executor tests hand their work to spawn workers, whose
lines our recorder cannot attribute to a test. Running the tasks inline during
the coverage run attributes those lines to the test that submitted them.
"""

from __future__ import annotations

import concurrent.futures
import concurrent.futures.process
import pickle
from concurrent.futures import Future


class InlineExecutor:
    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        self._initializer = initializer
        self._initargs = initargs or ()
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            self._started = True
            if self._initializer is not None:
                self._initializer(*self._initargs)

    def submit(self, fn, /, *args, **kwargs):
        self._ensure_started()
        future: Future = Future()
        try:
            # Keep the pickle round trip: some changes break pickling of
            # expressions, and that is exactly what makes pool tests fail.
            call_args = pickle.loads(pickle.dumps((args, kwargs)))
            result = fn(*call_args[0], **call_args[1])
            future.set_result(pickle.loads(pickle.dumps(result)))
        except BaseException as error:  # noqa: BLE001 - mirrors pool semantics
            future.set_exception(error)
        return future

    def map(self, fn, *iterables, timeout=None, chunksize=1):
        self._ensure_started()
        results = []
        for item in map(fn, *iterables):
            results.append(pickle.loads(pickle.dumps(item)))
        return iter(results)

    def shutdown(self, wait=True, *, cancel_futures=False):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown()
        return False


def pytest_configure(config):
    concurrent.futures.ProcessPoolExecutor = InlineExecutor
    concurrent.futures.process.ProcessPoolExecutor = InlineExecutor
