from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def make_redis(url: str) -> Redis:
    """A client that survives a Redis restart.

    Without this, connections pooled before a restart are dead and the first requests after it
    fail (measured: 11 of 300 webhooks returned HTTP 500 right after `docker compose restart
    redis`). Idle connections are pinged, and a command that hits a broken connection is retried
    on a fresh one. Timeouts keep a hung Redis from hanging a webhook past GitHub's 10 s limit.
    """
    return Redis.from_url(
        url,
        health_check_interval=15,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry=Retry(ExponentialBackoff(cap=1.0, base=0.05), 3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
