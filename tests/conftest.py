import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory, monkeypatch):
    """Keep settings, history and reports out of the real user profile."""
    monkeypatch.setenv("JUNKZERO_DATA_DIR", str(tmp_path_factory.mktemp("junkzero-data")))
