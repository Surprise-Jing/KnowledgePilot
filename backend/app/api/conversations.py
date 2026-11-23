"""Conversation, streaming message and run trace routes. Owner: B.
Contract: docs/contracts.md §3.3 and §4."""

from fastapi import APIRouter

router = APIRouter(tags=["conversations"])
