"""Usage-budget reservations, not financial balances; TigerBeetle remains the money store."""
from __future__ import annotations

import asyncpg
from common import OfferError


class PostgresCostLedger:
    def __init__(self, pool): self.pool = pool
    @classmethod
    async def connect(cls, database_url: str):
        if not database_url: raise RuntimeError("DATABASE_URL is required")
        return cls(await asyncpg.create_pool(database_url, min_size=1, max_size=5, command_timeout=30))
    async def close(self): await self.pool.close()
    async def spent_minor(self, tenant_id: str, job_id: str | None) -> int:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id',$1,true)", tenant_id)
                if job_id is None:
                    value = await connection.fetchval("SELECT COALESCE(SUM(amount_minor),0) FROM offer_cost_reservations WHERE tenant_id=$1", tenant_id)
                else:
                    value = await connection.fetchval("SELECT COALESCE(SUM(amount_minor),0) FROM offer_cost_reservations WHERE tenant_id=$1 AND job_id=$2", tenant_id, job_id)
        return int(value)
    async def reserve(self, tenant_id: str, job_id: str, amount_minor: int, idempotency_key: str) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id',$1,true)", tenant_id)
                existing = await connection.fetchrow("SELECT tenant_id,job_id,amount_minor FROM offer_cost_reservations WHERE idempotency_key=$1", idempotency_key)
                if existing:
                    if (existing["tenant_id"], existing["job_id"], existing["amount_minor"]) != (tenant_id, job_id, amount_minor):
                        raise OfferError("cost reservation replay conflict")
                    return
                await connection.execute("INSERT INTO offer_cost_reservations(tenant_id,job_id,amount_minor,idempotency_key) VALUES ($1,$2,$3,$4)",
                                          tenant_id, job_id, amount_minor, idempotency_key)
