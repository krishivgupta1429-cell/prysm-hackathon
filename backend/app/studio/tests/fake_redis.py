"""Hackathon-built (Meta Global AI Developer Hackathon).

A tiny in-memory stand-in for the async Redis client, with just the commands the Studio safety net uses.
"""


class FakeRedis:
    def __init__(self):
        self.data: dict[str, float | int | str] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        value = self.data.get(key)
        return None if value is None else str(value)

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def decr(self, key):
        self.data[key] = int(self.data.get(key, 0)) - 1
        return self.data[key]

    async def incrbyfloat(self, key, amount):
        self.data[key] = float(self.data.get(key, 0.0)) + float(amount)
        return self.data[key]

    async def expire(self, key, seconds):
        self.ttl[key] = seconds
        return True

    async def setex(self, key, seconds, value):
        self.data[key] = value
        self.ttl[key] = seconds

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    def keys_with(self, fragment):
        return {k: v for k, v in self.data.items() if fragment in k}
