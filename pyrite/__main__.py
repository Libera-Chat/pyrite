import asyncio
import json
from argparse import ArgumentParser
from pathlib import Path

from ircrobots.params import ConnectionParams, SASLUserPass
from ircrobots.security import TLSNoVerify, TLSVerifyChain

from . import Pyrite
from .config import Config


async def main(config: Config):
    bot = Pyrite(config)

    params = ConnectionParams.from_hoststring(config.nickname, config.server)
    params.username = config.username
    params.realname = config.realname
    params.password = config.password
    params.sasl = SASLUserPass(config.sasl.user, config.sasl.password)
    if config.tls_verify:
        TLS = TLSVerifyChain
    else:
        TLS = TLSNoVerify
    params.tls = TLS()

    autojoin = config.channels
    if config.log:
        autojoin.append(config.log)
    try:
        with Path(config.invite_cache).open("r") as f:
            try:
                cache = json.load(f)
            except json.decoder.JSONDecodeError:
                cache = {}
            print("[*] cache:", cache)
            if "channels" in cache:
                autojoin += cache["channels"]
    except FileNotFoundError:
        pass
    params.autojoin = autojoin

    await bot.add_server("pyrite", params)
    await bot.run()


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    config = Config.from_file(args.config)
    asyncio.run(main(config))
