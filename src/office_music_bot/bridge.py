from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from pathlib import Path
from typing import TextIO

from .config import Settings, load_settings, save_settings, validate_settings
from .runtime import BotRunner, RuntimeEvent, error_message

log = logging.getLogger(__name__)


def _settings(request: dict) -> Settings:
    values = request.get("settings")
    if not isinstance(values, dict):
        raise ValueError("Missing settings")
    fields = [values.get(key, "") for key in ("token", "guild_id", "channel_id")]
    if not all(isinstance(value, str) for value in fields):
        raise ValueError("Settings must contain strings")
    if any(len(value) > 2000 for value in fields):
        raise ValueError("Setting too long")
    return Settings(*fields)


def handle_request(request: dict, directory: Path, runner: BotRunner) -> dict:
    response = {"kind": "response", "id": request.get("id"), "ok": True}
    try:
        command = request.get("command")
        if command == "load":
            settings = load_settings(directory)
            response["settings"] = {
                "token": settings.token,
                "guild_id": settings.guild_id,
                "channel_id": settings.channel_id,
            }
        elif command in {"save", "start"}:
            if runner.busy:
                raise ValueError("Stop the bot before changing settings")
            settings = _settings(request)
            save_settings(directory, settings)
            if command == "start":
                runner.start(validate_settings(settings, directory))
            response["message"] = "設定已儲存，下次開啟會自動帶入。"
        elif command in {"stop", "quit"}:
            runner.stop()
        elif command == "login":
            runner.start_login(directory)
            response["message"] = "正在開啟 YouTube Music 登入視窗。"
        else:
            raise ValueError("Unknown command")
    except Exception as exc:
        log.error("Bridge request failed: %s", type(exc).__name__)
        response.update(ok=False, message=error_message(exc))
    return response


def run_bridge(
    directory: Path,
    *,
    source: TextIO | None = None,
    destination: TextIO | None = None,
    runner: BotRunner | None = None,
) -> int:
    source = source or sys.stdin
    destination = destination or sys.stdout
    runner = runner or BotRunner()
    requests: queue.SimpleQueue[str | None] = queue.SimpleQueue()

    def read() -> None:
        for line in source:
            requests.put(line)
        requests.put(None)

    def send(value: dict) -> None:
        destination.write(json.dumps(value, ensure_ascii=False) + "\n")
        destination.flush()

    threading.Thread(target=read, name="bridge-input", daemon=True).start()
    try:
        while True:
            try:
                line = requests.get(timeout=0.1)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                try:
                    if len(line) > 10000:
                        raise ValueError("Oversized request")
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("Invalid request object")
                except ValueError:
                    log.error("Invalid bridge request")
                    send({"kind": "error", "message": "內部通訊資料無效，請重新開啟程式。"})
                    continue
                send(handle_request(request, directory, runner))
                if request.get("command") == "quit":
                    break
            while True:
                try:
                    event: RuntimeEvent = runner.events.get_nowait()
                except queue.Empty:
                    break
                send({"kind": event.kind, "message": event.message, "data": event.data})
    except (BrokenPipeError, OSError):
        log.warning("Desktop connection closed; stopping owned bot session.")
    finally:
        runner.stop()
        if runner.thread:
            runner.thread.join(20)
        if runner.busy:
            log.error("Bot shutdown exceeded 20 seconds; desktop will terminate its owned backend.")
    return 0
