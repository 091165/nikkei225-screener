"""config/longterm.yaml の読み込み。"""

import copy
import os

import yaml

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "config", "longterm.yaml")


def load_config(path=None):
    """設定を dict で返す。path を省略すると config/longterm.yaml を読む。"""
    with open(path or _DEFAULT_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def override(cfg, **changes):
    """ドット区切りのキーで一部の値を差し替えたコピーを返す（テスト・検証用）。

    例: override(cfg, **{"crash_point.index_jp.threshold": -7.0})
    """
    new = copy.deepcopy(cfg)
    for dotted, value in changes.items():
        node = new
        keys = dotted.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return new
