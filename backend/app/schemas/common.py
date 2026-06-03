"""Shared schema primitives reused across all routers."""
from typing import Generic, Optional, TypeVar
from pydantic import BaseModel
from beanie import PydanticObjectId
from fastapi import HTTPException


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


def to_oid(id_str: str) -> PydanticObjectId:
    """Parse a string into a PydanticObjectId; raise HTTP 400 on bad format."""
    try:
        return PydanticObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid id: {id_str!r}")


def not_found(resource: str, id_str: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} '{id_str}' not found")


def make_pages(total: int, page_size: int) -> int:
    return max(1, -(-total // page_size))  # ceiling division
