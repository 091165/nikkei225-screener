import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="RUN_NETWORK_TESTS=1 のときだけ実行（Yahoo Finance に接続）")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
