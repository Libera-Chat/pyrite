import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SaslConfig:
    user: str
    password: str

    @classmethod
    def from_toml(cls, toml: dict[str, str]):
        return cls(
            user=toml["user"],
            password=toml["pass"],
        )


@dataclass
class Config:
    server: str
    nickname: str
    username: str
    realname: str
    password: str | None
    channels: list[str]
    log: str | None

    sasl: SaslConfig

    timeout: float
    tls_verify: bool
    allow_invite: bool
    response_file: str
    invite_cache: str
    command_allowmask: str

    @classmethod
    def from_file(cls, fp: str | Path):
        fp = Path(fp)
        with fp.open('rb') as f:
            config_toml = tomllib.load(f)

        irc_toml = config_toml["irc"]
        settings_toml = config_toml.get("settings", dict())
        sasl = SaslConfig.from_toml(config_toml["sasl"])

        return cls(
            server=irc_toml["server"],
            nickname=irc_toml["nickname"],
            username=irc_toml["username"],
            realname=irc_toml["realname"],
            password=irc_toml.get("pass"),
            channels=irc_toml["channels"],
            log=irc_toml.get("log"),
            sasl=sasl,
            timeout=settings_toml.get("timeout", 5),
            tls_verify=settings_toml.get("tls_verify", True),
            allow_invite=settings_toml.get("allow_invite", False),
            response_file=settings_toml.get("response_file", "./responses.json"),
            invite_cache=settings_toml.get("invite_cache", "./channels.json"),
            command_allowmask=settings_toml.get("command_allowmask", "libera/staff/*")
        )
