import pytest


@pytest.fixture(autouse=True)
def patchbay_home(tmp_path_factory, monkeypatch):
    """Each test gets its own ~/.patchbay, so tests never touch the user's plugins or
    config. Runs started by a test inherit it through the environment."""
    home = tmp_path_factory.mktemp("patchbay_home")
    monkeypatch.setenv("PATCHBAY_HOME", str(home))
    return home
