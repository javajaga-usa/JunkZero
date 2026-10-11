"""Shared test helpers: an API client carrying the app token, and fake scan results."""
from types import SimpleNamespace
from typing import Iterable

from fastapi.testclient import TestClient

from app import main
from app.config import CAT_TEMP_JUNK
from app.engine.classifier import build_item


def api_client(**kwargs) -> TestClient:
    """A client for JunkZero's API, sending this launch's app token like the real page does."""
    headers = {main.TOKEN_HEADER: main.API_TOKEN, **kwargs.pop("headers", {})}
    return TestClient(main.app, base_url="http://127.0.0.1", headers=headers, **kwargs)


def offer(paths: Iterable[str], category: str = CAT_TEMP_JUNK, **fields):
    """Make the given paths the current scan results, so /api/clean accepts them."""
    items = []
    for path in paths:
        path = str(path)
        item = build_item(path, path.replace("\\", "/").rsplit("/", 1)[-1], category, fields.get("size_bytes", 1),
                          1.0, "Safe", "test item", is_dir=fields.get("is_dir", False))
        for key, value in fields.items():
            if hasattr(item, key):
                setattr(item, key, value)
        items.append(item)
    main.current_scanner = SimpleNamespace(garbage_items=items, stats=SimpleNamespace(is_running=False))
    return items
