"""Batch operations handler.

Executes multiple commands in parallel for check-in/check-out prep.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List

LOGGER = logging.getLogger(__name__)


async def handle_batch(
    command_handler: Callable[
        [str, str, Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]
    ],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a batch of commands in parallel, return consolidated results."""
    commands: List[Dict[str, Any]] = params.get("commands", [])
    if not commands:
        raise ValueError("commands list is required and must not be empty")

    max_concurrency = min(params.get("max_concurrency", 5), 10)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def run_one(cmd: Dict[str, Any]) -> Dict[str, Any]:
        action = cmd.get("action", "")
        cmd_params = cmd.get("params", {})
        cmd_id = cmd.get("id", action)

        async with semaphore:
            try:
                result = await command_handler(action, cmd_id, cmd_params)
                return {
                    "id": cmd_id,
                    "action": action,
                    "status": "ok",
                    "data": result,
                }
            except Exception as exc:
                LOGGER.warning("Batch command failed: action=%s error=%s", action, exc)
                return {
                    "id": cmd_id,
                    "action": action,
                    "status": "error",
                    "error": {"code": "command_failed", "message": str(exc)},
                }

    results = await asyncio.gather(*(run_one(cmd) for cmd in commands))

    succeeded = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "error")

    return {
        "results": list(results),
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
    }
