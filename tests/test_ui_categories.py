"""The UI must filter on the exact category names the backend emits."""
import html
import re
from pathlib import Path

from app import config

INDEX = Path(__file__).resolve().parent.parent / "app" / "ui" / "index.html"
BACKEND_CATEGORIES = {
    value for name, value in vars(config).items() if name.startswith("CAT_")
}


def _ui_categories():
    source = INDEX.read_text(encoding="utf-8")
    cards = re.findall(r'data-category="([^"]+)"', source)
    select = re.search(r'<select id="categoryFilter".*?</select>', source, re.S).group(0)
    options = [v for v in re.findall(r'<option value="([^"]+)"', select) if v != "ALL"]
    return [html.unescape(v) for v in cards + options]


def test_every_ui_category_matches_a_backend_category():
    unknown = [c for c in _ui_categories() if c not in BACKEND_CATEGORIES]
    assert unknown == []


def test_every_backend_category_is_filterable():
    assert BACKEND_CATEGORIES <= set(_ui_categories())
