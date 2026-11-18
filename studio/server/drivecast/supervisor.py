"""Background services with heartbeats. Each service runs in its own thread and
restarts itself after an error with growing back-off, so one broken service
never takes the others (or the web panel) down."""
import logging
import threading
import time

from . import db

log = logging.getLogger("drivecast.supervisor")
HEARTBEATS = {}
_stop = threading.Event()


class Service(threading.Thread):
    def __init__(self, name, fn, interval):
        super().__init__(name=f"dc-{name}", daemon=True)
        self.svc, self.fn, self.interval = name, fn, interval
        self.errors = 0

    def run(self):
        while not _stop.is_set():
            HEARTBEATS[self.svc] = {"at": time.time(), "state": "running", "errors": self.errors, "every": self.interval}
            try:
                self.fn()
                self.errors = 0
                wait = self.interval
            except Exception as e:
                self.errors += 1
                wait = min(600, self.interval * (2 ** min(self.errors, 5)))
                log.exception("service %s failed", self.svc)
                HEARTBEATS[self.svc] = {"at": time.time(), "state": "error", "errors": self.errors, "last_error": str(e)[:200],
                                        "every": self.interval}
                if self.errors in (1, 5, 20):
                    db.event("error", "service", f"Background service “{self.svc}” hit an error and will retry: {e}")
            HEARTBEATS[self.svc]["at"] = time.time()
            _stop.wait(wait)


class WorkerLoop(threading.Thread):
    def __init__(self, n, run_job):
        super().__init__(name=f"dc-worker-{n}", daemon=True)
        self.n, self.run_job = n, run_job

    def run(self):
        from . import worker
        name = f"worker{self.n}"
        while not _stop.is_set():
            HEARTBEATS[name] = {"at": time.time(), "state": "idle"}
            try:
                job = worker.claim(name)
            except Exception:
                log.exception("claim failed")
                job = None
            if not job:
                _stop.wait(5)
                continue
            HEARTBEATS[name] = {"at": time.time(), "state": "busy", "channel_id": job["channel_id"]}
            try:
                self.run_job(job)
            except Exception as e:  # last line of defence: close the job, keep the loop alive
                log.exception("job %s crashed", job["id"])
                try:
                    worker.finish(job, "failed", f"Crashed: {e}", "failed", "Unexpected error — will try the next slot")
                except Exception:
                    pass


def start(services, workers=2):
    from . import worker
    worker.recover_interrupted()
    threads = [Service(n, fn, iv) for n, fn, iv in services]
    threads += [WorkerLoop(i + 1, worker.run) for i in range(workers)]
    for t in threads:
        t.start()
    return threads


def stop():
    _stop.set()
