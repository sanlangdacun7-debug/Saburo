"""返答づくりを裏側で進め、画面は途中経過を取りに来る仕組み。

スマホが途中でスリープしたり電波が切れたりしても、処理（特に数分かかる投資会議）は止まらない。
画面を開き直せば続きから表示できる。Cloudflare のクイックトンネルのように、
ストリーミング（SSE）を通さない経路でも動く。
"""

import secrets
import threading
import time
from collections.abc import Callable, Iterator

KEEP_FINISHED_SECONDS = 60 * 60


class Busy(Exception):
    """同じ会話で返答を作っている最中に、次の発言が来た。"""


class Job:
    def __init__(self, conversation_id: int):
        self.id = secrets.token_urlsafe(12)
        self.conversation_id = conversation_id
        self.events: list[dict] = []
        self.done = False
        self.finished_at: float | None = None
        self.cond = threading.Condition()

    def add(self, event: dict) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.finished_at = time.monotonic()
            self.cond.notify_all()

    def read(self, after: int, wait: float) -> dict:
        """after 番目以降のイベントを返す。まだなければ最大 wait 秒待つ。"""
        with self.cond:
            if len(self.events) <= after and not self.done:
                self.cond.wait(timeout=wait)
            return {"events": self.events[after:], "next": len(self.events), "done": self.done}


class JobRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def _cleanup(self) -> None:
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if job.done and job.finished_at and now - job.finished_at > KEEP_FINISHED_SECONDS:
                del self._jobs[job_id]

    def start(self, conversation_id: int, produce: Callable[[], Iterator[dict]]) -> Job:
        with self._lock:
            self._cleanup()
            if self.active_for(conversation_id, _locked=True):
                raise Busy()
            job = Job(conversation_id)
            self._jobs[job.id] = job

        def work():
            try:
                for event in produce():
                    job.add(event)
            except Exception as e:  # 想定外のエラーも画面に出す
                job.add({"type": "error", "text": f"内部エラー: {e}"})
            finally:
                job.finish()

        threading.Thread(target=work, daemon=True, name=f"job-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_for(self, conversation_id: int, _locked: bool = False) -> Job | None:
        def find():
            return next((j for j in self._jobs.values()
                         if j.conversation_id == conversation_id and not j.done), None)
        if _locked:
            return find()
        with self._lock:
            return find()
