from pydantic import BaseModel


class Page[T](BaseModel):
    """One page of a list the server sorted, searched and cut: `total` is the whole match, not the page."""

    items: list[T]
    total: int
