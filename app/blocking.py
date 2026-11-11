"""Finish filesystem/database work before cancellation releases its owner."""
import asyncio


async def run_blocking(function, /, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling the waiter cannot stop a thread still writing or holding a lock.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
