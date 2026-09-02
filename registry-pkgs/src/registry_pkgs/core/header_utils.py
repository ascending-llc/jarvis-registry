from typing import Any


def normalize_headers(config_headers: Any) -> dict[str, str]:
    """
    Normalize headers from dict or list-of-dict into a flat dict[str, str].
    """
    if isinstance(config_headers, dict):
        header_dicts = [config_headers]
    elif isinstance(config_headers, list):
        header_dicts = config_headers
    else:
        header_dicts = []

    normalized: dict[str, str] = {}
    for header_dict in header_dicts:
        if isinstance(header_dict, dict):
            for key, value in header_dict.items():
                if isinstance(value, list):
                    normalized[key] = ", ".join(str(v) for v in value)
                elif value is not None:
                    normalized[key] = str(value)

    return normalized
