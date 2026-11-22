import asyncio
import os
import signal
import subprocess
import sys

import pytest

from app.channels.telegram_intake import save_update
from app.channels.telegram_turns import TelegramTurns, UNKNOWN_OUTCOME
from app.telegram_worker import process_turn
from tests.integration.helpers import connect
from tests.integration.test_telegram_processors import until
from tests.integration.test_telegram_production import ready_updates, register, update


@pytest.mark.asyncio
@pytest.mark.parametrize("graceful", [True, False])
async def test_process_signals_drain_or_leave_an_interrupted_claim(scheduling_db, rabbit_url, tmp_path, graceful):
    url = scheduling_db["url"]
    bot = register(url)
    started, release = tmp_path / "started", tmp_path / "release"
    save_update(url, bot, update(1, text="slow turn"))
    ready_updates(url)
    code = """
import sys, time
from pathlib import Path
import app.telegram_worker as worker
from app.telegram_service import main
started, release = map(Path, sys.argv[1:])
def turn(turns, claim, runtime):
    with worker._keep_turn_claim_alive(turns, claim):
        started.touch()
        while not release.exists():
            time.sleep(0.02)
        turns.finish(claim, 'Drained reply', 'checkpoint')
worker.process_turn = turn
sys.argv = ['processor', 'process']
main()
"""
    env = {**os.environ, "TELEGRAM_BOT_ID": "42", "TELEGRAM_BOT_TOKEN": "42:synthetic"}
    process = subprocess.Popen([sys.executable, "-c", code, str(started), str(release)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await until(started.exists, timeout=8)
        process.send_signal(signal.SIGTERM if graceful else signal.SIGKILL)
        if graceful:
            await asyncio.sleep(0.1)
            assert process.poll() is None
            release.touch()
        await asyncio.to_thread(process.wait, timeout=5)
        assert process.returncode == (0 if graceful else -signal.SIGKILL)
        with connect(url) as connection:
            assert connection.execute("SELECT status FROM telegram_inbox").fetchone()["status"] == (
                "processed" if graceful else "processing"
            )
            if not graceful:
                connection.execute("UPDATE telegram_chat_processing_state SET lease_until=now()-interval '1 second'")
        if not graceful:
            turns = TelegramTurns(url, bot)
            claim = turns.claim_next()
            assert claim.interrupted
            process_turn(turns, claim, None)
        with connect(url) as connection:
            row = connection.execute("SELECT message FROM telegram_outbox").fetchone()
            assert row["message"] == ("Drained reply" if graceful else UNKNOWN_OUTCOME)
    finally:
        release.touch()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
