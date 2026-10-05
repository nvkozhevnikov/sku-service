"""Bounded atomic checkpoint replacement, tolerant of transient Windows readers."""
import json
import time


def write_checkpoint(path, state, *, attempts=20, delay=0.1):
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(attempts):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            # Windows readers/antivirus can briefly deny ReplaceFile. Never
            # delete the destination or discard the durable pending checkpoint.
            if attempt + 1 == attempts:
                raise
            time.sleep(delay)
