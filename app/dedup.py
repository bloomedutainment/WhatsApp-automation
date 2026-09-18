"""
Deduplication tracking using Upstash Redis (REST-based, over HTTPS).

Both is_downloaded/mark_downloaded and is_uploaded/mark_uploaded are
used with a SHA-256 content hash as the key (computed while streaming
the file in from WhatsApp) - NOT the media_id. This means the same
photo/video content is only ever saved and uploaded once, even if it
arrives under a different media_id each time (resent, forwarded, or
a retried webhook delivery).

Uses Upstash's REST API instead of the raw Redis TCP protocol - this
works over standard HTTPS (port 443), which is far less likely to be
blocked or interfered with by local networks/antivirus than Redis's
normal non-standard TCP port.

Uses SET with nx=True (SET if Not eXists) for the "mark as done"
calls - atomic, so two near-simultaneous requests for the same hash
can't both think they're "first".
"""

import os
from upstash_redis import Redis

UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# How long to remember a hash before it's eligible to be processed
# again. 7 days is generous.
DEDUP_TTL_SECONDS = 7 * 24 * 60 * 60

_client = None


def get_client():
    global _client
    if _client is None:
        _client = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
    return _client


def is_downloaded(file_hash: str) -> bool:
    """Check whether a file with this exact content hash has already
    been saved locally before (under any media_id)."""
    return get_client().exists(f"downloaded:{file_hash}") == 1


def mark_downloaded(file_hash: str) -> bool:
    """Mark this content hash as downloaded/saved. Returns True if
    this call is the one that set it, False if already marked."""
    result = get_client().set(f"downloaded:{file_hash}", "1", nx=True, ex=DEDUP_TTL_SECONDS)
    return result is not None


def is_uploaded(file_hash: str) -> bool:
    """Check whether a file with this exact content hash has already
    been uploaded to Drive before (under any media_id)."""
    return get_client().exists(f"uploaded:{file_hash}") == 1


def mark_uploaded(file_hash: str) -> bool:
    """Mark this content hash as uploaded to Drive. Returns True if
    this call is the one that set it, False if already marked."""
    result = get_client().set(f"uploaded:{file_hash}", "1", nx=True, ex=DEDUP_TTL_SECONDS)
    return result is not None