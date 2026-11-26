"""One authenticated POST -> bounded live events -> one validated commit."""
import asyncio
import concurrent.futures
import json
import socket
from threading import Event, Lock

from fastapi.responses import StreamingResponse

from .ai_errors import AIAPIError
from .ai_stream import StreamCancelled
from .storage import Conflict


class EventSink:
    def __init__(self, loop, include_reasoning=True):
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=32)
        self.include_reasoning = include_reasoning
        self.cancelled = Event()
        self.connection = None
        self.connection_lock = Lock()

    def check(self):
        if self.cancelled.is_set():
            raise StreamCancelled()

    def __call__(self, event):
        self.check()
        if not self.include_reasoning and event['type'] in ('reasoning', 'reasoning_limit'):
            return
        future = asyncio.run_coroutine_threadsafe(self.queue.put(event), self.loop)
        try:
            future.result(timeout=5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise StreamCancelled() from None
        self.check()

    def attach_connection(self, connection):
        with self.connection_lock:
            self.check()
            self.connection = connection

    def detach_connection(self, connection):
        with self.connection_lock:
            if self.connection is connection:
                self.connection = None

    def cancel(self):
        self.cancelled.set()
        with self.connection_lock:
            connection, self.connection = self.connection, None
        if connection is not None:
            try:
                if connection.sock:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
            connection.close()

    def commit(self, action):
        # After this checkpoint a completed SQLite commit may outlive a lost browser response.
        self.check()
        return action()


def stream_response(job, include_reasoning=True):
    def encode(event):
        return (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode()

    async def events():
        sink = EventSink(asyncio.get_running_loop(), include_reasoning)

        def run():
            try:
                result = job(sink)
                sink({'type': 'result', 'result': result})
            except StreamCancelled:
                pass
            except (AIAPIError, ValueError, KeyError, Conflict) as error:
                if not sink.cancelled.is_set():
                    event = {'type': 'error', 'error': str(error).strip("'"), 'conflict': isinstance(error, Conflict)}
                    if isinstance(error, AIAPIError):
                        event['ai_error'] = error.public()
                    try: sink(event)
                    except StreamCancelled: pass
            except Exception:
                if not sink.cancelled.is_set():
                    try: sink({'type': 'error', 'error': 'Подготовка не завершена. Сохранённая версия доступна; автоповтора нет.'})
                    except StreamCancelled: pass
            finally:
                if not sink.cancelled.is_set():
                    try: sink({'type': 'end'})
                    except StreamCancelled: pass

        worker = asyncio.create_task(asyncio.to_thread(run))
        try:
            yield encode({'type': 'start'})
            while True:
                try:
                    event = await asyncio.wait_for(sink.queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield encode({'type': 'heartbeat'})
                    continue
                if event['type'] == 'end':
                    break
                yield encode(event)
        finally:
            sink.cancel()
            # The worker observes cancellation and closes transport; it must not be replayed.
            worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    return StreamingResponse(events(), media_type='application/x-ndjson', headers={
        'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
        'X-Content-Type-Options': 'nosniff',
    })
