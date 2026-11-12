"""Module for checking SDK version against the latest release."""

import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

LATEST_VERSION_URL = "https://opensource.tbank.ru/api/v4/projects/238/releases"


def parse_version(version_str: str) -> Tuple[int, ...]:
    version_str = version_str.lstrip("v")
    try:
        return tuple(int(x) for x in version_str.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def get_current_version():
    from . import __version__

    return __version__


def get_latest_version() -> Optional[str]:
    try:
        response = requests.get(
            LATEST_VERSION_URL,
            headers={"User-Agent": "tinkoff-invest-python-sdk"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as exc:
        logger.debug("Failed to fetch latest version: %s", exc)
        return None

    if not isinstance(data, list) or not data:
        return None

    latest_release = data[0]
    if not isinstance(latest_release, dict):
        return None

    tag_name = latest_release.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        return None

    return tag_name[1:] if tag_name.startswith("v") else tag_name


def check_version(current_version: str) -> Optional[str]:
    latest_version = get_latest_version()
    if not latest_version:
        return None

    current_parsed = parse_version(current_version)
    latest_parsed = parse_version(latest_version)

    if latest_parsed > current_parsed:
        return latest_version
    return None


def get_version_warning() -> Optional[str]:
    current_version = get_current_version()
    latest_version = check_version(current_version)
    if latest_version:
        return (
            f"WARNING: A newer version of t-tech-investments python SDK is available. \n"  # noqa: E501
            f"Current: {current_version}, Latest: {latest_version}. \n"
            f"Please consider upgrading."
        )
    return None
