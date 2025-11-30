from ...core.config_manager import Config

# Make sure suffix is always a string (e.g., "" if None)
_SUFFIX: str = str(getattr(Config, "CMD_SUFFIX", "") or "")


class BotCommands:
    # Deep-link start param (for t.me/<bot>?start=premium-...)
    DeepLinkPremiumParam = "premium"

    # Core
    StartCommand       = f"start{_SUFFIX}"
    HelpCommand        = f"help{_SUFFIX}"
    PingCommand        = f"ping{_SUFFIX}"
    RestartCommand     = f"restart{_SUFFIX}"
    StatsCommand       = f"stats{_SUFFIX}"
    LogCommand         = f"log{_SUFFIX}"

    # File / task
    MirrorCommand      = [f"mirror{_SUFFIX}", f"m{_SUFFIX}"]
    QbMirrorCommand    = [f"qbmirror{_SUFFIX}", f"qm{_SUFFIX}"]
    JdMirrorCommand    = [f"jdmirror{_SUFFIX}", f"jm{_SUFFIX}"]
    YtdlCommand        = [f"ytdl{_SUFFIX}", f"y{_SUFFIX}"]
    NzbMirrorCommand   = [f"nzbmirror{_SUFFIX}", f"nm{_SUFFIX}"]

    LeechCommand       = [f"leech{_SUFFIX}", f"l{_SUFFIX}"]
    QbLeechCommand     = [f"qbleech{_SUFFIX}", f"ql{_SUFFIX}"]
    JdLeechCommand     = [f"jdleech{_SUFFIX}", f"jl{_SUFFIX}"]
    YtdlLeechCommand   = [f"ytdlleech{_SUFFIX}", f"yl{_SUFFIX}"]
    NzbLeechCommand    = [f"nzbleech{_SUFFIX}", f"nl{_SUFFIX}"]

    CloneCommand       = f"clone{_SUFFIX}"
    CountCommand       = f"count{_SUFFIX}"
    DeleteCommand      = f"del{_SUFFIX}"
    CancelTaskCommand  = [f"cancel{_SUFFIX}", f"c{_SUFFIX}"]
    CancelAllCommand   = f"cancelall{_SUFFIX}"
    ForceStartCommand  = [f"forcestart{_SUFFIX}", f"fs{_SUFFIX}"]
    ListCommand        = f"list{_SUFFIX}"
    SearchCommand      = f"search{_SUFFIX}"
    StatusCommand      = f"status{_SUFFIX}"

    # Users/admin
    UsersCommand       = f"users{_SUFFIX}"
    AuthorizeCommand   = f"auth{_SUFFIX}"
    UnAuthorizeCommand = f"unauth{_SUFFIX}"
    AddSudoCommand     = f"addsudo{_SUFFIX}"
    RmSudoCommand      = f"rmsudo{_SUFFIX}"
    ShellCommand       = f"shell{_SUFFIX}"
    AExecCommand       = f"aexec{_SUFFIX}"
    ExecCommand        = f"exec{_SUFFIX}"
    ClearLocalsCommand = f"clearlocals{_SUFFIX}"

    # Settings
    BotSetCommand      = [f"bsetting{_SUFFIX}", f"bs{_SUFFIX}"]
    UserSetCommand     = [f"usetting{_SUFFIX}", f"us{_SUFFIX}"]
    SelectCommand      = f"sel{_SUFFIX}"

    # Feeds / NZB
    RssCommand         = f"rss{_SUFFIX}"
    NzbSearchCommand   = f"nzbsearch{_SUFFIX}"

    # Premium / usage
    BuyPremiumCommand  = f"premium{_SUFFIX}"   # chat command to open premium menu
    SetPremiumCommand  = f"setpremium{_SUFFIX}"
    UsageCommand       = f"usage{_SUFFIX}"
    MyFilesCommand     = f"myfiles{_SUFFIX}"
    AboutCommand       = f"about{_SUFFIX}"
    InviteCommand      = f"invite{_SUFFIX}"
