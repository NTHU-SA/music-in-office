from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord

from office_music_bot import __version__
from office_music_bot.browser import PlaybackError, probe
from office_music_bot.config import (
    Config,
    ConfigurationError,
    InstanceLock,
    data_directory,
    load_config,
    setup,
)
from office_music_bot.discord_bot import MusicBot


def configure_logging(directory: Path, *, console: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handlers = [
        RotatingFileHandler(
            directory / "bot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        ),
    ]
    if console and sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("discord").setLevel(logging.WARNING)


async def run(config: Config) -> None:
    async with MusicBot(config) as bot:
        await bot.start(config.token)
        if bot.fatal_error:
            raise PlaybackError(bot.fatal_error)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Discord requests -> local Windows YouTube Music")
    parser.add_argument("--version", action="version", version=f"OfficeMusicBot {__version__}")
    parser.add_argument(
        "--setup", action="store_true", help="Replace local protected configuration"
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without connecting"
    )
    parser.add_argument(
        "--probe-browser",
        action="store_true",
        help="Check live Edge song search without Discord/audio",
    )
    parser.add_argument("--console", action="store_true", help="Use the legacy console mode")
    parser.add_argument("--bridge", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.bridge:
        from office_music_bot.bridge import run_bridge

        directory = data_directory()
        configure_logging(directory, console=False)
        return run_bridge(directory)
    try:
        directory = data_directory()
        configure_logging(directory)
        with InstanceLock(directory):
            if args.probe_browser:
                asyncio.run(probe(directory))
                return 0
            if args.setup:
                setup(directory)
                return 0
            if (
                not args.check
                and not (directory / "config.json").exists()
                and not os.environ.get("OFFICE_MUSIC_TOKEN")
            ):
                if not sys.stdin.isatty():
                    raise ConfigurationError(
                        "First-run setup needs an interactive console. Run --setup locally."
                    )
                setup(directory)
            config = load_config(directory)
            if args.check:
                print(
                    f"Configuration valid. Server: {config.guild_id}; channel: {config.channel_id}"
                )
                return 0
            print("OfficeMusicBot running. Keep both Edge tabs open. Press Ctrl+C to stop.")
            asyncio.run(run(config))
            return 0
    except KeyboardInterrupt:
        print("\nOfficeMusicBot stopped.")
        return 0
    except (ConfigurationError, PlaybackError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
    except discord.LoginFailure:
        print(
            "ERROR: Discord rejected the token. Run --setup with a valid bot token.",
            file=sys.stderr,
        )
    except discord.HTTPException as exc:
        print(
            f"ERROR: Discord HTTP {exc.status}. Check IDs, invite, permissions and connection.",
            file=sys.stderr,
        )
    except (OSError, EOFError):
        print(
            "ERROR: Local I/O or setup failed. Run from a terminal and check bot.log.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
