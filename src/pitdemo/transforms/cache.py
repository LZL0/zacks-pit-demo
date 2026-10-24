"""Content-addressed consensus cache.

The key hashes every input that can change the answer: the query itself plus the
per-security data versions and the function version. Tables are append-only, so a
version only ever goes up. A new split, estimate or earnings release produces a new
key, and the stale entry is simply never asked for again. There is no invalidation
logic to get wrong; old entries age out of the LRU.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from pitdemo.schemas import Basis, ConsensusResult, Versions


def cache_key(
    security_id: str,
    period: str,
    as_of: dt.datetime,
    basis: Basis,
    known_as_of: dt.datetime | None,
    versions: Versions,
) -> str:
    payload = {
        "security_id": security_id,
        "period": period,
        "as_of": as_of.isoformat(),
        "basis": basis,
        "known_as_of": known_as_of.isoformat() if known_as_of else None,
        "estimates_version": versions.estimates,
        "factors_version": versions.factors,
        "reports_version": versions.reports,
        "fn_version": versions.fn,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0


class ConsensusCache:
    def __init__(self, maxsize: int = 4096) -> None:
        self.maxsize = maxsize
        self._data: OrderedDict[str, ConsensusResult] = OrderedDict()
        self.stats = CacheStats()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get_or_compute(self, key: str, compute: Callable[[], ConsensusResult]) -> ConsensusResult:
        hit = self._data.get(key)
        if hit is not None:
            self._data.move_to_end(key)
            self.stats.hits += 1
            return hit
        self.stats.misses += 1
        value = compute()
        self._data[key] = value
        if len(self._data) > self.maxsize:
            self._data.popitem(last=False)
        return value
