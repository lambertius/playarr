"""Async supervisor for durable local outboxes and the single-process actor."""
from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.database import SerializedSessionLocal
from app.services.contribution_outbox import process_next_contribution
from app.services.file_operations import (
    reconcile_file_operations,
    retry_waiting_file_operation,
)
from app.services.mutation_runtime import (
    process_next_mutation,
    recover_mutation_queue,
)
from app.services.sidecar_outbox import process_next_sidecar
from app.services.sidecar_outbox import schedule_stale_sidecars

logger = logging.getLogger(__name__)


async def durable_reconciler() -> None:
    settings = get_settings()
    db = SerializedSessionLocal()
    try:
        file_recovery = await asyncio.to_thread(reconcile_file_operations, db)
        if file_recovery["recovered"] or file_recovery["unresolved"]:
            logger.warning("File operation recovery: %s", file_recovery)
    finally:
        db.close()
    if settings.deployment_profile == "single_process":
        recovered = await asyncio.to_thread(recover_mutation_queue, SerializedSessionLocal)
        if recovered:
            logger.warning("Recovered %s interrupted mutation command(s)", recovered)
    db = SerializedSessionLocal()
    try:
        repaired = await asyncio.to_thread(schedule_stale_sidecars, db)
        if repaired:
            logger.warning("Scheduled %s missing or stale sidecar(s)", repaired)
    finally:
        db.close()
    reconciliation_ticks = 0
    while True:
        mutation = False
        if settings.deployment_profile == "single_process":
            mutation = await asyncio.to_thread(process_next_mutation, SerializedSessionLocal)
        sidecar = await asyncio.to_thread(process_next_sidecar, SerializedSessionLocal)
        contribution = await asyncio.to_thread(process_next_contribution, SerializedSessionLocal)
        db = SerializedSessionLocal()
        try:
            file_operation = await asyncio.to_thread(retry_waiting_file_operation, db)
            reconciliation_ticks += 1
            if reconciliation_ticks >= 60:
                await asyncio.to_thread(schedule_stale_sidecars, db)
                reconciliation_ticks = 0
        finally:
            db.close()
        await asyncio.sleep(
            0.05 if mutation or sidecar or contribution or file_operation else 1.0
        )
