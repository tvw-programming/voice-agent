"""The voice agent against the real Express API in student_api/.

Skipped unless Node.js is installed and `npm install` has been run in student_api/.
"""
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from voice_agent.tools.registry import Session, ToolRegistry

API_DIR = Path(__file__).resolve().parents[1] / "student_api"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (API_DIR / "node_modules" / "express").exists(),
    reason="Node.js or student_api/node_modules missing (run: cd student_api && npm install)")


@pytest.fixture(scope="module")
def api_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "PORT": str(port), "STUDENT_API_KEY": "test-key"}
    proc = subprocess.Popen(["node", "server.js"], cwd=API_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(url + "/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url + "/api/v1"
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def registry(cfg, monkeypatch, api_url):
    monkeypatch.setenv("STUDENT_API_BASE_URL", api_url)
    monkeypatch.setenv("STUDENT_API_KEY", "test-key")
    session = Session("4321")
    session.authenticated = True
    return ToolRegistry(cfg, session)


async def lookup(reg, **args):
    return await reg.call("get_student_details", {"confirmed_by_user": True, **args})


async def test_name(registry):
    r = await lookup(registry, first_name="Priya", last_name="Kulkarni")
    assert r["status"] == "found" and r["student"]["roll_number"] == 27
    assert "phone" not in r["student"] and "parent_contact" not in r["student"]


async def test_class_division_roll(registry):
    r = await lookup(registry, class_name="eighth", division="A", roll_number="twelve")
    assert r["status"] == "found" and r["student"]["full_name"] == "Aarav Deshmukh"


async def test_first_name_in_class(registry):
    r = await lookup(registry, first_name="Aarav", class_name="8", division="A")
    assert r["status"] == "found"


async def test_misheard_surname_via_variant(registry):
    r = await lookup(registry, first_name="Aarav", last_name="Desmukh", class_name="6", division="C")
    assert r["status"] == "found" and r["student"]["roll_number"] == 4


async def test_spelled_name(registry):
    r = await lookup(registry, first_name="Ishaan", last_name_spelled="B for Bombay H A double T")
    assert r["status"] == "found"
