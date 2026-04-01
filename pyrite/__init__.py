import asyncio
import json
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from cachetools import TTLCache

import ircrobots
from irctokens import build, Line
from ircstates.numerics import RPL_ISUPPORT

from .config import Config
from .irc import Caller, command, on_message, Server

__version__ = "0.1.0"


@dataclass
class Responses:
    default: dict[str, str] = field(default_factory=dict)
    silly: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str):
        with Path(path).open() as f:
            data = json.load(f)
            return cls(**data)

    def get(self, state: str) -> str:
        if secrets.randbelow(100) < 69:
            pool = self.default
        else:
            pool = self.silly
        try:
            return secrets.choice(pool[state])
        except KeyError:
            return secrets.choice(self.silly["other"])

    def __repr__(self) -> str:
        return ("default: "
                f"{len(self.default['active'])} active, "
                f"{len(self.default['paused'])} paused, "
                f"{len(self.default['done'])} done; "
                "silly: "
                f"{len(self.silly['active'])} active, "
                f"{len(self.silly['paused'])} paused, "
                f"{len(self.silly['done'])} done, "
                f"{len(self.silly['other'])} other")


def get_target(line: Line) -> str:
    match line.command:
        case "PRIVMSG" | "NOTICE" | "TAGMSG":
            return line.params[0]
        case _:
            raise NotImplementedError(f"target selection not implemented for {line.command}")


class PyriteServer(Server):
    def __init__(self, bot: ircrobots.Bot, name: str, config: Config):
        super().__init__(bot, name, config)

        self.typing_cache: dict[str, TTLCache[str, tuple[str, float]]] = defaultdict(lambda: TTLCache(maxsize=10000, ttl=30)) # TODO: or 6?

        try:
            self.responses = Responses.from_file(self._config.response_file)
            print(f"[*] loaded responses: {self.responses}")
        except Exception as e:
            self.responses = Responses()
            print(f"[!] failed to load responses from {self._config.response_file}: {e}")

    def update_typing_cache(self, target: str, nick: str, new_value: str):
        self.typing_cache[target][nick] = (new_value, time.monotonic())

    async def _batch_joins(self, channels: list[str], batch_n: int = 10):
        self.send_nick(self._config.nickname)
        await asyncio.sleep(5)
        return await super()._batch_joins(channels, batch_n)

    # message handlers {{{

    @on_message(RPL_ISUPPORT)
    async def on_isupport(self, _):
        """finish initialisation and print a message to console"""
        if not self._init and self.isupport.network:
            print(f"[*] connected to {self.isupport.network} as {self.nickname}")
            self._init = True

    @on_message("PRIVMSG",
                lambda ln: ln.source is not None and len(ln.params) > 0 and ln.params[-1].startswith("\x01"))
    async def on_ctcp(self, line: Line):
        """respond to CTCP queries"""
        query = line.params[-1].strip("\x01").split()
        if not query:
            return
        command = query[0].upper()
        match command:
            case "VERSION":
                resp = f"VERSION pyrite v{__version__}"
            case _:
                resp = ""

        if resp:
            self.send(build("NOTICE", [line.hostmask.nickname, f"\x01{resp}\x01"]))

    @on_message("PRIVMSG",
                lambda ln: ln.source is not None and ln.tags is not None)
    async def expire_cache_on_send(self, line: Line):
        if (target := get_target(line)).lower() == self.nickname.lower():
            return
        nick = line.hostmask.nickname
        if nick in self.typing_cache[target]:
            del self.typing_cache[target][nick]

    @on_message("TAGMSG",
                lambda ln: ln.source is not None and len(ln.params) > 0 and ln.tags is not None)
    async def on_typing(self, line: Line):
        """handle tag messages"""
        if line.tags is None or line.source is None:
            return
        if not (typing := line.tags.get("+typing")):
            return
        if (target := get_target(line)).lower() == self.nickname.lower() or \
            line.hostmask.nickname.lower() == self.nickname.lower():
            return
        if target == self._config.log:
            return

        old_typing, old_time = self.typing_cache[target].get(line.hostmask.nickname, ("", -1))
        if abs(time.monotonic() - old_time) < 3.0: # basic rate limit
            return

        if typing != old_typing:
            self.update_typing_cache(target, line.hostmask.nickname, typing)
            self.send(build("NOTICE", [target, self.responses.get(typing).format(nick=line.hostmask.nickname, channel=target)]))

        if typing == "active": # troll typing
            self.send(build("TAGMSG", [target], tags={"+typing": "active"}))

    @on_message("INVITE",
                lambda ln: ln.source is not None)
    async def on_invite(self, line: Line):
        if not self._config.allow_invite:
            return

        sender = line.hostmask
        chan = line.params[-1]
        self.log(f"invited to join {chan} at the request of {sender}")
        self.send_join(chan)

    @on_message("JOIN",
                lambda ln: ln.source is not None)
    async def on_join(self, line: Line):
        if line.hostmask.nickname == self.nickname:
            chan = line.params[0]
            self.log(f"joined {chan}")
            if chan not in self._config.channels:
                with Path(self._config.invite_cache).open("w+") as f:
                    try:
                        cache = json.load(f)
                    except json.decoder.JSONDecodeError:
                        cache = {}
                    if "channels" not in cache:
                        cache["channels"] = []
                    if chan not in cache["channels"]:
                        cache["channels"].append(chan)
                    json.dump(cache, f)

    @on_message("KICK",
                lambda ln: ln.source is not None)
    async def on_kick(self, line: Line):
        if line.params[1] != self.nickname:
            return
        chan = line.params[0]
        self.log(f"kicked from {chan} (reason: {line.params[-1]})")
        with Path(self._config.invite_cache).open("w+") as f:
            try:
                cache = json.load(f)
            except json.decoder.JSONDecodeError:
                cache = {}
            if "channels" not in cache:
                cache["channels"] = []
            if chan in cache["channels"]:
                cache["channels"].remove(chan)
            json.dump(cache, f)

    # }}}

    # command handlers {{{

    @command("REHASH")
    async def _reload_responses(self, caller: Caller, _: list[str]):
        """
        usage: REHASH
          reload the bot's responses (staff only)
        """
        try:
            self.responses = Responses.from_file(self._config.response_file)
            return f"successfully loaded responses from {self._config.response_file}: {self.responses}"
        except Exception as e:
            return f"failed to load responses from {self._config.response_file}: {e}"

    # }}}


class Pyrite(ircrobots.Bot):
    def __init__(self, config: Config):
        super().__init__()
        self._config = config

    def create_server(self, name: str):
        return PyriteServer(self, name, self._config)
