import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory, monkeypatch):
    """Keep settings, history and reports out of the real user profile."""
    monkeypatch.setenv("JUNKZERO_DATA_DIR", str(tmp_path_factory.mktemp("junkzero-data")))


@pytest.fixture(autouse=True)
def no_open_files(monkeypatch):
    """Deletes in tests don't depend on what else is running on the machine (lsof is slow on macOS)."""
    from app.engine import cleaner
    from app.engine.inuse import OpenFileCheck
    monkeypatch.setattr(cleaner, "OpenFileCheck", lambda: OpenFileCheck(open_files=set()))
