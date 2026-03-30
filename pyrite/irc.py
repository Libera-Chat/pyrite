import asyncio
import inspect
import shlex
import traceback
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any, Callable, Coroutine, TypeAlias

import ircrobots
from ircrobots import ircv3
from irctokens import build, Line

from .config import Config

CAP_MSG_TAGS = ircv3.Capability("message-tags")


@dataclass
class Caller:
    nick: str
    source: str


MsgHandler: TypeAlias = Callable[[Any, Line], Coroutine[Any, Any, None]]


class OnMessage:
    def __init__(self, commands: str | tuple[str, ...], predicate: Callable[[Line], bool] | None = None):
        """
        This class should be used as a decorator: @on_message(irc_command, optional_predicate).
        This will register a method of Server as a message handler. On each received
        irc message, message handlers will be filtered by the command and predicate,
        then run concurrently in asyncio.gather()ed tasks. The predicate can only filter
        based on the line itself, so some additional filter may be required within the
        handler itself. The irc command name is case-insensitive. Message handlers should
        be coroutines and receive the server object and the line as function arguments.
        """
        if isinstance(commands, str):
            self.commands = (commands.upper(),)
        else:
            self.commands = tuple(cmd.upper() for cmd in commands)
        self.predicate = predicate if predicate is not None else lambda _: True

    def __call__(self, handler):
        self.handler = handler
        self.name = handler.__name__
        return self

    def run(self, cls: ircrobots.Server, line: Line):
        return self.handler(cls, line)

    def __repr__(self) -> str:
        return f"<message handler {self.name!r} for {self.commands!r}>"


on_message = OnMessage


CmdHandler: TypeAlias = Callable[[Any, Caller, list[str] | str], Coroutine[Any, Any, str]]


class Command:
    def __init__(self, name: str):
        """
        This class should be used as a decorator: @command(command_name).
        This will register a method of Server as a command handler. On each
        received irc message, the Server.on_command() message handler will
        try to match each received command to a handler and run that handler.
        Commands are accepted in the formats "<nick>: <command>" and similar,
        or by any private message. Command names are case-insensitive.
        Command handlers should be coroutines and receive the server object,
        the command's caller, and a list of string arguments as function arguments.
        """
        self.name = name.lower()

    def __call__(self, handler):
        self.handler = handler
        self.help = inspect.getdoc(self.handler) or f"no help available for '{self.name}'"
        return self

    def run(self, cls: ircrobots.Server, caller: Caller, args: list[str]):
        return self.handler(cls, caller, args)

    def __repr__(self) -> str:
        return f"<command handler for {self.name!r}>"


command = Command


class Server(ircrobots.Server):
    def __init__(self, bot: ircrobots.Bot, name: str, config: Config):
        super().__init__(bot, name)
        self.desired_caps |= {CAP_MSG_TAGS}
        self._init = False
        self._config = config
        self._cmd_handlers = {
            v.name.lower(): v for _, v in
            inspect.getmembers(type(self), predicate=lambda m: isinstance(m, Command))
        }
        self._msg_handlers = [
            h for _, h in inspect.getmembers(type(self), predicate=lambda m: isinstance(m, OnMessage))
        ]

        print("[*] registered command handlers:")
        print("\t" + ", ".join(self._cmd_handlers.keys()))
        print("[*] registered message handlers:")
        for h in self._msg_handlers:
            print(f"\t{', '.join(h.commands)} => {h.name}")

    def set_throttle(self, rate: int, time: float):
        # turn off throttling
        pass

    def log(self, text: str):
        """
        send a message to the log channel, if configured
        """
        if self._config.log:
            self.send_message(self._config.log, text)

    async def line_read(self, line: Line):
        """
        read a line from irc and dispatch the relevant handlers
        """
        handlers = [h for h in self._msg_handlers
                    if line.command in h.commands and h.predicate(line)]
        ret = await asyncio.gather(*(h.run(self, line) for h in handlers), return_exceptions=True)
        for i, e in enumerate(ret):
            if e is not None:
                print(f"[!] exception encountered in message handler {handlers[i].name!r}: {e}")
                traceback.print_tb(e.__traceback__)

    @on_message("PRIVMSG", lambda ln: ln.source is not None)
    async def on_command(self, line: Line):
        """
        try to process and respond to a command
        """
        if self.is_me(line.hostmask.nickname):
            return

        first, _, rest = line.params[1].partition(" ")

        if self.is_me(line.params[0]):
            # private message
            target = line.hostmask.nickname
            command = first
            sargs = rest

        elif rest and first in {f"{self.nickname}{c}" for c in [":", ",", ""]}:
            # highlight in channel
            command, _, sargs = rest.partition(" ")
            target = line.params[0]

        else:
            return

        if not line.tags or (line.hostmask.hostname and not fnmatch(line.hostmask.hostname, self._config.command_allowmask)):
            return

        caller = Caller(line.hostmask.nickname, str(line.hostmask))

        command = command.lower()
        if command not in self._cmd_handlers.keys():
            return

        try:
            sh = shlex.shlex(sargs, posix=True)
            # don't remove backslashes
            sh.escape = ''
            # only split on whitespace, like shlex.split()
            sh.whitespace_split = True
            args = list(sh)
        except ValueError as e:
            self.send(build("NOTICE", [target, f"shlex failure: {str(e)}"]))
            return

        try:
            outs = await self._cmd_handlers[command].run(self, caller, args)
        except Exception as e:
            print(f"[!] exception encountered in command handler {command!r}: {e}")
            traceback.print_tb(e.__traceback__)
        else:
            if isinstance(outs, str):
                outs = outs.strip().split("\n")
            for out in outs:
                await self.send(build("NOTICE", [target, out]))
