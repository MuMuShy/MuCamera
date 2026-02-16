import redis.asyncio as aioredis
from typing import Optional, Dict, Any
import json
import logging
from app.config import settings

logger = logging.getLogger("mumucam")


class RedisClient:
    """Redis client for presence tracking and session management"""

    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.enabled = settings.REDIS_ENABLED
        self._memory_store: Dict[str, str] = {}  # In-memory fallback

    async def connect(self):
        """Connect to Redis"""
        if not self.enabled:
            return
        try:
            self.redis = await aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True
            )
            await self.redis.ping()
        except Exception as e:
            print(f"Redis connection failed: {e}. Using in-memory fallback.")
            self.redis = None

    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis:
            await self.redis.close()

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """Set a key-value pair"""
        try:
            if self.redis:
                await self.redis.set(key, json.dumps(value), ex=ex)
                return True
            else:
                # In-memory fallback (no expiration support)
                self._memory_store[key] = json.dumps(value)
                return True
        except Exception as e:
            print(f"Redis SET error: {e}")
            return False

    async def setex(self, key: str, seconds: int, value: Any) -> bool:
        """Set a key-value pair with expiration (same as set with ex parameter)"""
        try:
            if self.redis:
                await self.redis.setex(key, seconds, json.dumps(value))
                return True
            else:
                # In-memory fallback (no expiration support)
                self._memory_store[key] = json.dumps(value)
                return True
        except Exception as e:
            print(f"Redis SETEX error: {e}")
            return False

    async def get(self, key: str) -> Optional[Any]:
        """Get a value by key"""
        try:
            if self.redis:
                value = await self.redis.get(key)
                return json.loads(value) if value else None
            else:
                value = self._memory_store.get(key)
                return json.loads(value) if value else None
        except Exception as e:
            print(f"Redis GET error: {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete a key"""
        try:
            if self.redis:
                await self.redis.delete(key)
                return True
            else:
                self._memory_store.pop(key, None)
                return True
        except Exception as e:
            print(f"Redis DELETE error: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        try:
            if self.redis:
                return await self.redis.exists(key) > 0
            else:
                return key in self._memory_store
        except Exception as e:
            print(f"Redis EXISTS error: {e}")
            return False

    async def hset(self, name: str, key: str, value: Any) -> bool:
        """Set hash field"""
        try:
            if self.redis:
                await self.redis.hset(name, key, json.dumps(value))
                return True
            else:
                hash_key = f"hash:{name}"
                if hash_key not in self._memory_store:
                    self._memory_store[hash_key] = json.dumps({})
                hash_data = json.loads(self._memory_store[hash_key])
                hash_data[key] = value
                self._memory_store[hash_key] = json.dumps(hash_data)
                return True
        except Exception as e:
            print(f"Redis HSET error: {e}")
            return False

    async def hget(self, name: str, key: str) -> Optional[Any]:
        """Get hash field"""
        try:
            if self.redis:
                value = await self.redis.hget(name, key)
                return json.loads(value) if value else None
            else:
                hash_key = f"hash:{name}"
                if hash_key in self._memory_store:
                    hash_data = json.loads(self._memory_store[hash_key])
                    return hash_data.get(key)
                return None
        except Exception as e:
            print(f"Redis HGET error: {e}")
            return None

    async def hdel(self, name: str, *keys: str) -> bool:
        """Delete hash fields"""
        try:
            if self.redis:
                await self.redis.hdel(name, *keys)
                return True
            else:
                hash_key = f"hash:{name}"
                if hash_key in self._memory_store:
                    hash_data = json.loads(self._memory_store[hash_key])
                    for key in keys:
                        hash_data.pop(key, None)
                    self._memory_store[hash_key] = json.dumps(hash_data)
                return True
        except Exception as e:
            print(f"Redis HDEL error: {e}")
            return False

    async def hgetall(self, name: str) -> Dict[str, Any]:
        """Get all hash fields"""
        try:
            if self.redis:
                data = await self.redis.hgetall(name)
                return {k: json.loads(v) for k, v in data.items()}
            else:
                hash_key = f"hash:{name}"
                if hash_key in self._memory_store:
                    return json.loads(self._memory_store[hash_key])
                return {}
        except Exception as e:
            print(f"Redis HGETALL error: {e}")
            return {}

    async def health_check(self) -> Dict[str, Any]:
        """Check Redis health: role, writability, and auto-fix slave mode"""
        result = {"healthy": True, "writable": True, "role": "unknown", "error": None}

        if not self.redis:
            result.update(healthy=False, writable=False, role="disconnected",
                          error="Redis not connected (using in-memory fallback)")
            return result

        try:
            # Check replication role
            info = await self.redis.info("replication")
            role = info.get("role", "unknown")
            result["role"] = role

            if role != "master":
                result["healthy"] = False
                result["writable"] = False
                result["error"] = f"Redis is running as '{role}' instead of 'master'"
                logger.error(f"Redis role is '{role}' — attempting REPLICAOF NO ONE to recover")
                try:
                    await self.redis.execute_command("REPLICAOF", "NO", "ONE")
                    # Verify recovery
                    info_after = await self.redis.info("replication")
                    new_role = info_after.get("role", "unknown")
                    if new_role == "master":
                        result.update(healthy=True, writable=True, role="master",
                                      error=f"Auto-recovered from '{role}' to 'master'")
                        logger.warning(f"Redis auto-recovered from '{role}' to 'master'")
                    else:
                        logger.error(f"Redis recovery failed, still '{new_role}'")
                except Exception as fix_err:
                    logger.error(f"Failed to execute REPLICAOF NO ONE: {fix_err}")
                    result["error"] += f"; auto-fix failed: {fix_err}"
                return result

            # Verify write capability
            try:
                await self.redis.set("_health_check_test", "OK", ex=10)
                result["writable"] = True
            except Exception as write_err:
                result["healthy"] = False
                result["writable"] = False
                result["error"] = f"Redis write test failed: {write_err}"

        except Exception as e:
            result.update(healthy=False, writable=False, error=str(e))

        return result


# Global Redis client instance
redis_client = RedisClient()
