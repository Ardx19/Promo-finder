from pathlib import Path

import pytest

from promo_finder.config import load_config
from promo_finder.parse import load_show

FIXTURES = Path(__file__).parent / "fixtures"
SHOW_DIR = FIXTURES / "synthetic_show"


@pytest.fixture
def show():
    return load_show(SHOW_DIR)


@pytest.fixture
def cfg():
    return load_config()
