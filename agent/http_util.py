"""Bounded retry with exponential backoff for the public register APIs.

Budget-conscious: at most MAX_RETRIES extra attempts per call, so even a
fully degraded run stays far inside the competition's 2,000 requests/day
limit (100 companies x ~3 sources x 3 attempts = 900 worst case).
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

MAX_RETRIES = 2          # total attempts per call = 1 + MAX_RETRIES
BACKOFF_SECONDS = 1.0


def get_json(url: str, timeout: float = 8.0) -> Optional[Any]:
    """Return parsed JSON, or None on 404/400/410/any unrecoverable failure.
    Retries only on timeouts, connection errors, and 5xx."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (400, 404, 410):
                return None  # definitive "no", don't waste retries
            # 5xx etc.: fall through and retry
        except (requests.exceptions.RequestException, ValueError):
            pass
        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_SECONDS * (2 ** attempt))
    return None