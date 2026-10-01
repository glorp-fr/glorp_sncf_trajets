"""Constants for SNCF Trajets."""

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "sncf_trajets"
VERSION = "0.1.0"

API_BASE = "https://api.sncf.com/v1/coverage/sncf"
NAVITIA_TZ = ZoneInfo("Europe/Paris")
API_KEY_URL = "https://numerique.sncf.com/startup/api/token-developpeur/"

CONF_FROM_ID = "from_id"
CONF_FROM_NAME = "from_name"
CONF_TO_ID = "to_id"
CONF_TO_NAME = "to_name"
CONF_START = "start"
CONF_END = "end"
CONF_WEEKDAYS = "weekdays"
CONF_COUNT = "count"
CONF_NOTIFY = "notify"
CONF_THRESHOLD = "threshold"
CONF_QUIET_START = "quiet_start"
CONF_QUIET_END = "quiet_end"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_COUNT = 3
DEFAULT_THRESHOLD = 5
DEFAULT_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
DEFAULT_QUIET_START = "22:00:00"
DEFAULT_QUIET_END = "06:00:00"

DELAY_CHANGE_STEP = 5
LOOKBACK = timedelta(minutes=30)

INTERVAL_ACTIVE = timedelta(minutes=2)
INTERVAL_IDLE = timedelta(minutes=15)
INTERVAL_NIGHT = timedelta(minutes=60)
INTERVAL_MAX = timedelta(minutes=60)
PRE_WINDOW = timedelta(minutes=60)

EVENT_ALERT = "sncf_trajets_alert"
CARD_URL = "/sncf_trajets/sncf-trajets-card.js"
