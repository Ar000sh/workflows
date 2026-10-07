import pytest

from lead_responder.practice import load_practice


@pytest.fixture
def practice():
    return load_practice()
