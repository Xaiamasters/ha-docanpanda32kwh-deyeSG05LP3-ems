"""Engine state with atomic persistence and durable write-intent records."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path


class MemoryState:
    def __init__(self, data=None):
        self.data = copy.deepcopy(data or {})
        self.data.setdefault('events', [])

    @property
    def latch(self):
        return self.data.get('latch')

    @property
    def state(self):
        return self.data.get('state', {})

    def flush(self):
        pass

    def journal(self, row):
        self.data['events'].append(copy.deepcopy(row))
        self.data['events'] = self.data['events'][-500:]
        self.flush()

    def persist(self, row):
        self.data['state'] = copy.deepcopy(row)
        self.flush()

    def set_latch(self, row):
        self.data['latch'] = copy.deepcopy(row)
        self.flush()

    def save_plan(self, row):
        self.data.setdefault('plans', {})[row['for_date']] = copy.deepcopy(row)
        self.data['plans'] = dict(sorted(self.data['plans'].items())[-3:])
        self.flush()


class FileState(MemoryState):
    """A private file under this integration's storage directory only."""
    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('invalid_engine_state')
        except FileNotFoundError:
            data = {}
        except (ValueError, OSError):
            # Losing an existing latch must never look like a fresh installation.
            data = {'latch': {'why': 'engine state unreadable; owner review required'}}
        super().__init__(data)

    def flush(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        if os.name != 'nt':
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
