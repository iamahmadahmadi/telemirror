from importlib import import_module
from ast import literal_eval
from os import getenv

from bot import LOGGER


class Config:
    ADSGRAM_URL = ""
    TELEGA_URL = ""
    PREMIUM_USERS = ""
    DAILY_LIMIT_FREE_MB = 2048
    DAILY_LIMIT_PREMIUM_MB = 51200
    NSFW_DOMAINS = ""
    USER_TRANSMISSION = True
    AS_DOCUMENT = False
    AUTHORIZED_CHATS = ""
    BASE_URL = ""
    BASE_URL_PORT = 80
    BOT_TOKEN = ""
    CMD_SUFFIX = ""
    DATABASE_URL = ""
    DEFAULT_UPLOAD = "rc"
    EQUAL_SPLITS = False
    EXCLUDED_EXTENSIONS = ""
    FFMPEG_CMDS = {}
    FILELION_API = ""
    GDRIVE_ID = ""
    INCOMPLETE_TASK_NOTIFIER = False
    INDEX_URL = ""
    IS_TEAM_DRIVE = False
    JD_EMAIL = ""
    JD_PASS = ""
    LEECH_DUMP_CHAT = ""
    LEECH_FILENAME_PREFIX = ""
    LEECH_SPLIT_SIZE = 2097152000
    MEDIA_GROUP = False
    HYBRID_LEECH = False
    HYDRA_IP = ""
    HYDRA_API_KEY = ""
    NAME_SUBSTITUTE = ""
    OWNER_ID = 0
    QUEUE_ALL = 0
    QUEUE_DOWNLOAD = 0
    QUEUE_UPLOAD = 0
    RCLONE_FLAGS = ""
    RCLONE_PATH = ""
    RCLONE_SERVE_URL = ""
    RCLONE_SERVE_USER = ""
    RCLONE_SERVE_PASS = ""
    RCLONE_SERVE_PORT = 8080
    RSS_CHAT = ""
    RSS_DELAY = 600
    RSS_SIZE_LIMIT = 0
    SEARCH_API_LINK = ""
    SEARCH_LIMIT = 0
    SEARCH_PLUGINS = []
    STATUS_LIMIT = 4
    STATUS_UPDATE_INTERVAL = 3
    STOP_DUPLICATE = False
    STREAMWISH_API = ""
    SUDO_USERS = ""
    TELEGRAM_API = 0
    TELEGRAM_HASH = ""
    TG_PROXY = {}
    THUMBNAIL_LAYOUT = ""
    TORRENT_TIMEOUT = 0
    UPLOAD_PATHS = {}
    UPSTREAM_REPO = ""
    UPSTREAM_BRANCH = "master"
    USENET_SERVERS = []
    USER_SESSION_STRING = ""
    USER_TRANSMISSION = False
    USE_SERVICE_ACCOUNTS = False
    WEB_PINCODE = False
    YT_DLP_OPTIONS = {}

    # --- Upload and download performance tuning ---
    # These settings allow advanced users to tweak the behavior of uploads and
    # downloads.  By default the bot is conservative to avoid hitting remote
    # service rate limits.  Increasing these values can improve throughput but
    # may cause more API errors if set too high.  All values below can be
    # overridden from `config.py` or environment variables.

    # Number of concurrent rclone transfers for uploads.  Higher values can
    # significantly improve upload speeds when using Google Drive or other
    # remotes that support parallel transfers.  The default remains 1 to
    # maintain backwards‑compatible behaviour.
    RCLONE_UPLOAD_TRANSFERS = 1

    # Transactions per second (TPS) limit for rclone uploads.  Google Drive
    # enforces API quotas that can be exceeded if this value is too high.  The
    # default of 1 corresponds to the original implementation.  Increase with
    # caution.
    RCLONE_UPLOAD_TPSLIMIT = 1

    # Burst rate for TPS limit.  Controls how many operations may be queued up
    # in short bursts.  Defaults to the same value as `RCLONE_UPLOAD_TRANSFERS`.
    RCLONE_UPLOAD_TPS_BURST = 1

    # Chunk size in megabytes used by rclone when uploading to Google Drive.
    # Larger chunk sizes reduce HTTP overhead and can increase throughput at
    # the expense of memory usage.  A value of 0 disables setting the flag
    # explicitly, leaving rclone’s default of 8M/0.  When greater than zero
    # this value will be passed as the `--drive-chunk-size` flag (e.g.
    # `128` becomes `--drive-chunk-size 128M`).
    RCLONE_UPLOAD_DRIVE_CHUNK_SIZE = 0

    # Number of concurrent rclone transfers for downloads.  Similar to the
    # upload variant but applied when downloading from remotes.
    RCLONE_DOWNLOAD_TRANSFERS = 1

    # TPS limit for rclone downloads.  See documentation above.
    RCLONE_DOWNLOAD_TPSLIMIT = 1

    # Burst rate for download TPS limit.
    RCLONE_DOWNLOAD_TPS_BURST = 1

    # Chunk size in megabytes used by rclone when downloading from Google Drive.
    # Behaviour is identical to `RCLONE_UPLOAD_DRIVE_CHUNK_SIZE` but for
    # downloads.
    RCLONE_DOWNLOAD_DRIVE_CHUNK_SIZE = 0

    # Chunk size in megabytes used by the Google Drive API client when
    # uploading files via the `googleapiclient`.  Larger chunks mean fewer
    # network requests but more memory consumption.  The default value of
    # 100 matches the previous implementation.  Setting this to a higher
    # value (e.g. 256) can significantly improve upload speeds on stable
    # connections.
    GDRIVE_CHUNK_SIZE_MB = 100

    @classmethod
    def _convert(cls, key: str, value):
        if not hasattr(cls, key):
            raise KeyError(f"{key} is not a valid configuration key.")

        expected_type = type(getattr(cls, key))

        if value is None:
            return None

        if isinstance(value, expected_type):
            return value

        if expected_type is bool:
            return str(value).strip().lower() in {"true", "1", "yes"}

        if expected_type in [list, dict]:
            if not isinstance(value, str):
                raise TypeError(
                    f"{key} should be {expected_type.__name__}, got {type(value).__name__}"
                )

            if not value:
                return expected_type()

            try:
                evaluated = literal_eval(value)
                if not isinstance(evaluated, expected_type):
                    raise TypeError(
                        f"Expected {expected_type.__name__}, got {type(evaluated).__name__}"
                    )
                return evaluated
            except (ValueError, SyntaxError, TypeError) as e:
                raise TypeError(
                    f"{key} should be {expected_type.__name__}, got invalid string: {value}"
                ) from e

        try:
            return expected_type(value)
        except (ValueError, TypeError) as exc:
            raise TypeError(
                f"Invalid type for {key}: expected {expected_type}, got {type(value)}"
            ) from exc

    @classmethod
    def get(cls, key: str):
        return getattr(cls, key, None)

    @classmethod
    def set(cls, key: str, value) -> None:
        if not hasattr(cls, key):
            raise KeyError(f"{key} is not a valid configuration key.")

        converted_value = cls._convert(key, value)
        setattr(cls, key, converted_value)

    @classmethod
    def get_all(cls):
        return {
            key: getattr(cls, key)
            for key in cls.__dict__.keys()
            if not key.startswith("__") and not callable(getattr(cls, key))
        }

    @classmethod
    def _is_valid_config_attr(cls, attr: str) -> bool:
        if attr.startswith("__") or callable(getattr(cls, attr, None)):
            return False
        return hasattr(cls, attr)

    @classmethod
    def _process_config_value(cls, attr: str, value):
        if not value:
            return None

        converted_value = cls._convert(attr, value)

        if isinstance(converted_value, str):
            converted_value = converted_value.strip()

        if attr == "DEFAULT_UPLOAD" and converted_value != "gd":
            return "rc"

        if attr in {
            "BASE_URL",
            "RCLONE_SERVE_URL",
            "INDEX_URL",
            "SEARCH_API_LINK",
        }:
            return converted_value.strip("/") if converted_value else ""

        if attr == "USENET_SERVERS" and (
            not converted_value or not converted_value[0].get("host")
        ):
            return None

        return converted_value

    @classmethod
    def _load_from_module(cls) -> bool:
        try:
            settings = import_module("config")
        except ModuleNotFoundError:
            return False

        for attr in dir(settings):
            if not cls._is_valid_config_attr(attr):
                continue

            raw_value = getattr(settings, attr)
            processed_value = cls._process_config_value(attr, raw_value)

            if processed_value is not None:
                setattr(cls, attr, processed_value)

        return True

    @classmethod
    def _load_from_env(cls) -> None:
        for attr in dir(cls):
            if not cls._is_valid_config_attr(attr):
                continue

            env_value = getenv(attr)
            if env_value is None:
                continue

            processed_value = cls._process_config_value(attr, env_value)
            if processed_value is not None:
                setattr(cls, attr, processed_value)

    @classmethod
    def _validate_required_config(cls) -> None:
        required_keys = ["BOT_TOKEN", "OWNER_ID", "TELEGRAM_API", "TELEGRAM_HASH"]

        for key in required_keys:
            value = getattr(cls, key)
            if isinstance(value, str):
                value = value.strip()
            if not value:
                raise ValueError(f"{key} variable is missing!")

    @classmethod
    def load(cls) -> None:
        if not cls._load_from_module():
            LOGGER.info(
                "Config module not found, loading from environment variables..."
            )
            cls._load_from_env()

        cls._validate_required_config()

    @classmethod
    def load_dict(cls, config_dict) -> None:
        for key, value in config_dict.items():
            if not hasattr(cls, key):
                continue

            processed_value = cls._process_config_value(key, value)

            if key == "USENET_SERVERS" and processed_value is None:
                processed_value = []

            if processed_value is not None:
                setattr(cls, key, processed_value)

        cls._validate_required_config()