"""Dedicated Redis mutation queue entry point."""
from app.worker import celery_app


@celery_app.task(name="app.mutation_tasks.process_mutation_queue")
def process_mutation_queue(limit: int = 100) -> int:
    from app.services.mutation_runtime import process_next_mutation
    processed = 0
    while processed < limit and process_next_mutation():
        processed += 1
    return processed
