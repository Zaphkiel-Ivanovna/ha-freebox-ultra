"""Constants for the Freebox Ultra integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "freebox_ultra"
MANUFACTURER: Final = "Freebox SAS"

APP_ID: Final = "ha_freebox_ultra"
APP_NAME: Final = "Home Assistant (Freebox Ultra)"
APP_VERSION: Final = "0.1.0"

DEFAULT_HOST: Final = "mafreebox.freebox.fr"
DEFAULT_PORT: Final = 443
DISCOVERY_URL: Final = "http://mafreebox.freebox.fr/api_version"
ZEROCONF_TYPE: Final = "_fbx-api._tcp.local."
CERT_FILENAME: Final = "freebox_certificates.pem"

HTTP_TIMEOUT: Final = 10
AUTH_TIMEOUT: Final = 180
AUTH_POLL_INTERVAL: Final = 2

API_MAJOR_MIN: Final = 8
API_MAJOR_MAX: Final = 12

CONF_APP_TOKEN: Final = "app_token"
CONF_API_DOMAIN: Final = "api_domain"
CONF_API_BASE_URL: Final = "api_base_url"
CONF_API_VERSION: Final = "api_version"
CONF_BOX_UID: Final = "box_uid"
CONF_BOX_MODEL: Final = "box_model"
CONF_BOX_MODEL_NAME: Final = "box_model_name"
CONF_DEVICE_NAME: Final = "device_name"

OPT_CATEGORIES: Final = "categories"
OPT_SCAN_INTERVALS: Final = "scan_intervals"
OPT_TRACK_NEW_DEVICES: Final = "track_new_devices"
OPT_CONSIDER_HOME: Final = "consider_home"

DEFAULT_CONSIDER_HOME: Final = 180

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


class Category(StrEnum):
    """A group of endpoints polled together by a single coordinator."""

    CONNECTION = "connection"
    SYSTEM = "system"
    LAN = "lan"
    WIFI = "wifi"
    STORAGE = "storage"
    HOME = "home"
    PHONE = "phone"
    CALLS = "calls"
    VPN = "vpn"
    PLAYER = "player"
    DOWNLOADS = "downloads"
    PROFILES = "profiles"


@dataclass(frozen=True, slots=True)
class CategoryMeta:
    """Static metadata driving the options flow and the coordinators."""

    interval: timedelta
    default_enabled: bool
    permission: str | None = None


CATEGORY_META: Final[dict[Category, CategoryMeta]] = {
    Category.CONNECTION: CategoryMeta(timedelta(seconds=15), True),
    Category.SYSTEM: CategoryMeta(timedelta(seconds=60), True),
    Category.LAN: CategoryMeta(timedelta(seconds=60), True),
    Category.WIFI: CategoryMeta(timedelta(seconds=120), False, "settings"),
    Category.STORAGE: CategoryMeta(timedelta(minutes=5), False, "settings"),
    Category.HOME: CategoryMeta(timedelta(seconds=30), False, "home"),
    Category.PHONE: CategoryMeta(timedelta(seconds=30), False, "settings"),
    Category.CALLS: CategoryMeta(timedelta(seconds=60), False, "calls"),
    Category.VPN: CategoryMeta(timedelta(minutes=2), False, "settings"),
    Category.PLAYER: CategoryMeta(timedelta(seconds=30), False, "player"),
    Category.DOWNLOADS: CategoryMeta(timedelta(seconds=30), False, "downloader"),
    Category.PROFILES: CategoryMeta(timedelta(minutes=2), False, "profile"),
}

DEFAULT_CATEGORIES: Final = [
    category for category, meta in CATEGORY_META.items() if meta.default_enabled
]


class HomeCategory(StrEnum):
    """`category` field of a /home/nodes/ entry."""

    ALARM = "alarm"
    BASIC_SHUTTER = "basic_shutter"
    CAMERA = "camera"
    DWS = "dws"
    IOHOME = "iohome"
    KFB = "kfb"
    OPENER = "opener"
    PIR = "pir"
    RTS = "rts"
    SHUTTER = "shutter"


HOME_CATEGORY_TO_MODEL: Final[dict[HomeCategory, str]] = {
    HomeCategory.ALARM: "F-MSEC07A",
    HomeCategory.CAMERA: "F-HACAM01A",
    HomeCategory.DWS: "F-HADWS01A",
    HomeCategory.KFB: "F-HAKFB01A",
    HomeCategory.PIR: "F-HAPIR01A",
    HomeCategory.IOHOME: "IOHome",
    HomeCategory.RTS: "RTS",
}

ALARM_EP_STATE: Final = "state"
ALARM_EP_ARM_AWAY: Final = "alarm1"
ALARM_EP_ARM_HOME: Final = "alarm2"
ALARM_EP_DISARM: Final = "off"
ALARM_EP_TRIGGER: Final = "trigger"
ALARM_EP_SKIP: Final = "skip"
ALARM_EP_PIN: Final = "pin"
ALARM_EP_SOUND: Final = "sound"
ALARM_EP_VOLUME: Final = "volume"
ALARM_EP_TIMEOUT_ARMING: Final = "timeout1"
ALARM_EP_TIMEOUT_SIREN_DELAY: Final = "timeout2"
ALARM_EP_TIMEOUT_SIREN_DURATION: Final = "timeout3"

ALARM_STATE_IDLE: Final = "idle"
ALARM_STATE_ARMING_AWAY: Final = "alarm1_arming"
ALARM_STATE_ARMED_AWAY: Final = "alarm1_armed"
ALARM_STATE_ARMING_HOME: Final = "alarm2_arming"
ALARM_STATE_ARMED_HOME: Final = "alarm2_armed"
ALARM_STATE_ALERT_TIMER_AWAY: Final = "alarm1_alert_timer"
ALARM_STATE_ALERT_TIMER_HOME: Final = "alarm2_alert_timer"
ALARM_STATE_ALERT: Final = "alert"


WS_PATH: Final = "ws/event/"
WS_EVENTS: Final = [
    "home_node_update",
    "phone_state_update",
    "call_state_update",
    "dhcp_lease_update",
    "download_task_update",
    "fs_tasks_update",
]
