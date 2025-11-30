# REQUIRED CONFIG
BOT_TOKEN = "5310077920:AAGf8wtBapssFrIXyt6_1rsI0x25MZBnI5g"
OWNER_ID = 84895335
TELEGRAM_API = 815825
TELEGRAM_HASH = "1711e54657d1787b4eb52c6d07ed428c"
# OPTIONAL CONFIG
TG_PROXY = {}
USER_SESSION_STRING = "AgAMctEArm68bj7xHHPw68esBT-iu8AwmHZtrmcaIUaf7Tk9gH5R0lpvWRDmXTl6strORCHGKtPwgFwgSvhhpdeRMA_2YNYSAVxM8bVW9KG4BOwatixje-o08lmNogYTTzMijeQV7L56Dt5LCYuBMZorkQcspueyQsMRcX-b3gihOqB_af1Vp_rWehtr9OYq4TOMsp_ZpLayHZkB5KsMfkZK7n-PUgyWAH4kQ_xaUqL5Uo_tKzqjC66sXPNwIWkLpM9QsGi-bDX60dKMyHqlmmU5mSEpY-MbPXFBHBYqB9mjY3lwc06puOiFHjWxakbquRnThhrR-v5EDjp40J3dFCEkFwrOQAAAAABFC5wjAA"
CMD_SUFFIX = ""
AUTHORIZED_CHATS = "84895335"
SUDO_USERS = "84895335"
DATABASE_URL = ""
STATUS_LIMIT = 4
DEFAULT_UPLOAD = "rc"
STATUS_UPDATE_INTERVAL = 15
FILELION_API = ""
STREAMWISH_API = ""
EXCLUDED_EXTENSIONS = ""
INCOMPLETE_TASK_NOTIFIER = False
YT_DLP_OPTIONS = ""
USE_SERVICE_ACCOUNTS = False
NAME_SUBSTITUTE = ""
FFMPEG_CMDS = {}
UPLOAD_PATHS = {}
# GDrive Tools
GDRIVE_ID = ""
IS_TEAM_DRIVE = False
STOP_DUPLICATE = False
INDEX_URL = ""
# Rclone
RCLONE_PATH = ""
RCLONE_FLAGS = ""
RCLONE_SERVE_URL = ""
RCLONE_SERVE_PORT = 0
RCLONE_SERVE_USER = ""
RCLONE_SERVE_PASS = ""
# JDownloader
JD_EMAIL = ""
JD_PASS = ""
# Sabnzbd
USENET_SERVERS = [
    {
        "name": "main",
        "host": "",
        "port": 563,
        "timeout": 60,
        "username": "",
        "password": "",
        "connections": 8,
        "ssl": 1,
        "ssl_verify": 2,
        "ssl_ciphers": "",
        "enable": 1,
        "required": 0,
        "optional": 0,
        "retention": 0,
        "send_group": 0,
        "priority": 0,
    }
]
# Nzb search
HYDRA_IP = ""
HYDRA_API_KEY = ""
# Update
UPSTREAM_REPO = ""
UPSTREAM_BRANCH = "master"
# Leech
LEECH_SPLIT_SIZE = 0
AS_DOCUMENT = False
EQUAL_SPLITS = False
MEDIA_GROUP = False
USER_TRANSMISSION = False
HYBRID_LEECH = False
LEECH_FILENAME_PREFIX = ""
LEECH_DUMP_CHAT = ""
THUMBNAIL_LAYOUT = ""
# qBittorrent/Aria2c
TORRENT_TIMEOUT = 0
BASE_URL = ""
BASE_URL_PORT = 0
WEB_PINCODE = False
# Queueing system
QUEUE_ALL = 0
QUEUE_DOWNLOAD = 0
QUEUE_UPLOAD = 0
# RSS
RSS_DELAY = 600
RSS_CHAT = ""
RSS_SIZE_LIMIT = 0
# Torrent Search
SEARCH_API_LINK = ""
SEARCH_LIMIT = 0
SEARCH_PLUGINS = [
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/piratebay.py",
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/limetorrents.py",
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/torlock.py",
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/torrentscsv.py",
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/eztv.py",
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/torrentproject.py",
    "https://raw.githubusercontent.com/MaurizioRicci/qBittorrent_search_engines/master/kickass_torrent.py",
    "https://raw.githubusercontent.com/MaurizioRicci/qBittorrent_search_engines/master/yts_am.py",
    "https://raw.githubusercontent.com/MadeOfMagicAndWires/qBit-plugins/master/engines/linuxtracker.py",
    "https://raw.githubusercontent.com/MadeOfMagicAndWires/qBit-plugins/master/engines/nyaasi.py",
    "https://raw.githubusercontent.com/LightDestory/qBittorrent-Search-Plugins/master/src/engines/ettv.py",
    "https://raw.githubusercontent.com/LightDestory/qBittorrent-Search-Plugins/master/src/engines/glotorrents.py",
    "https://raw.githubusercontent.com/LightDestory/qBittorrent-Search-Plugins/master/src/engines/thepiratebay.py",
    "https://raw.githubusercontent.com/v1k45/1337x-qBittorrent-search-plugin/master/leetx.py",
    "https://raw.githubusercontent.com/nindogo/qbtSearchScripts/master/magnetdl.py",
    "https://raw.githubusercontent.com/msagca/qbittorrent_plugins/main/uniondht.py",
    "https://raw.githubusercontent.com/khensolomon/leyts/master/yts.py",
]


# --- Added by custom patch: Premium, Quota, NSFW ---
# Space-separated Telegram user IDs who are premium (override DB; good for bootstrapping)
PREMIUM_USERS = ""
# Daily usage limits in MB
DAILY_LIMIT_FREE_MB = 2048
DAILY_LIMIT_PREMIUM_MB = 51200  # 50 GB
# Domains considered NSFW (only premium users can download from these)
NSFW_DOMAINS = "pornhub.com xvideos.com redtube.com xhamster.com nhentai.net gelbooru.com rule34.xxx"
# Use user session (USER_SESSION_STRING) for uploads by default (falls back to bot if unavailable)
USER_TRANSMISSION = True

##### Performance tuning options #####
# The following optional settings are provided to tune upload and download speeds.
# Default values are intentionally conservative to avoid hitting API rate limits.
# Increase these values if your environment can handle higher throughput.
# See bot/core/config_manager.py for detailed documentation.

# Number of concurrent rclone transfers for uploads.
RCLONE_UPLOAD_TRANSFERS = 1

# Rclone transactions per second (TPS) limit for uploads.
RCLONE_UPLOAD_TPSLIMIT = 1

# TPS burst for uploads.
RCLONE_UPLOAD_TPS_BURST = 1

# Chunk size in MB for rclone drive uploads. 0 means use rclone default.
RCLONE_UPLOAD_DRIVE_CHUNK_SIZE = 0

# Number of concurrent rclone transfers for downloads.
RCLONE_DOWNLOAD_TRANSFERS = 1

# Rclone TPS limit for downloads.
RCLONE_DOWNLOAD_TPSLIMIT = 1

# TPS burst for downloads.
RCLONE_DOWNLOAD_TPS_BURST = 1

# Chunk size in MB for rclone drive downloads. 0 means use rclone default.
RCLONE_DOWNLOAD_DRIVE_CHUNK_SIZE = 0

# Chunk size in MB used by the Google Drive API client when uploading.
GDRIVE_CHUNK_SIZE_MB = 100
