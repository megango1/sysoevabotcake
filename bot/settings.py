import json
import os

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
_DEFAULTS: dict = {"price": 389, "days": 30}


def _load() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE) as f:
                return {**_DEFAULTS, **json.load(f)}
        except Exception:
            pass
    return dict(_DEFAULTS)


def _save(data: dict) -> None:
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_price() -> int:
    return int(_load()["price"])


def get_days() -> int:
    return int(_load()["days"])


def set_price(price: int) -> None:
    s = _load()
    s["price"] = price
    _save(s)


def set_days(days: int) -> None:
    s = _load()
    s["days"] = days
    _save(s)
