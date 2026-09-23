"""Pre-flight check: python scripts/check_setup.py"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voice_agent.config import load_config  # noqa: E402

cfg = load_config()
ok = True


def report(label, good, detail=""):
    global ok
    ok &= good or label.startswith("(optional)")
    print(f"  [{'OK' if good else '--'}] {label} {detail}")


print("Python packages")
for mod, optional in [("openai", 0), ("anthropic", 0), ("httpx", 0), ("rapidfuzz", 0), ("num2words", 0),
                      ("sounddevice", 0), ("soundfile", 0), ("faster_whisper", 0), ("silero_vad", 0),
                      ("kokoro", 0), ("parler_tts", 1)]:
    try:
        importlib.import_module(mod)
        report(("(optional) " if optional else "") + mod, True)
    except Exception as e:  # noqa: BLE001
        report(("(optional) " if optional else "") + mod, False, f"-> {type(e).__name__}")

print("\nLM Studio (local LLM)")
import httpx  # noqa: E402
local = cfg["llm"]["local"]
try:
    r = httpx.get(local["base_url"] + "/models", timeout=3)
    ids = [m["id"] for m in r.json().get("data", [])]
    report("server reachable", True, local["base_url"])
    report(f"model '{local['model']}' available", local["model"] in ids, f"(found: {ids})")
except Exception as e:  # noqa: BLE001
    report("server reachable", False, f"-> {e}. Run scripts/setup_lmstudio")

print("\nCloud fallback keys (.env)")
for key in ["ANTHROPIC_API_KEY", "SARVAM_API_KEY"]:
    report(key, bool(os.environ.get(key)))

print("\nStudent API server (student_api/)")
import shutil  # noqa: E402
report("(optional) node", shutil.which("node") is not None, "-> https://nodejs.org or: brew install node")
report("(optional) student_api/node_modules",
       (Path(__file__).resolve().parents[1] / "student_api" / "node_modules" / "express").exists(),
       "-> cd student_api && npm install")

print("\nStudent API")
s = cfg["tools"]["student_lookup"]
base = os.environ.get(s["base_url_env"])
report(s["base_url_env"], bool(base), base or "")
staff_cfg = cfg.get("staff", {})
secret = os.environ.get(staff_cfg.get("secret_env", "STAFF_PIN_SECRET"))
report(staff_cfg.get("secret_env", "STAFF_PIN_SECRET"), bool(secret))
staff_file = Path(__file__).resolve().parents[1] / staff_cfg.get("file", "data/staff.json")
active = []
if staff_file.exists():
    import json as _json
    active = [x for x in _json.loads(staff_file.read_text(encoding="utf-8")).get("staff", []) if x.get("active", True)]
if active:
    report("staff enrolled", True, f"({len(active)} active)")
else:
    report(s["staff_pin_env"] + " (legacy shared PIN, no staff enrolled yet)", bool(os.environ.get(s["staff_pin_env"])),
           "-> add staff: python -m voice_agent.features.staff_pins add --name \"Full Name\"")
if base:
    try:
        httpx.get(base.rstrip("/") + s["endpoint"], params={"first_name": "test", "last_name": "test"},
                  headers={"Authorization": f"Bearer {os.environ.get(s['api_key_env'], '')}"}, timeout=3)
        report("student API reachable", True)
    except Exception as e:  # noqa: BLE001
        report("student API reachable", False, f"-> {e}")

print("\nAudio devices")
try:
    import sounddevice as sd
    report("default input", True, str(sd.query_devices(kind="input")["name"]))
    report("default output", True, str(sd.query_devices(kind="output")["name"]))
except Exception as e:  # noqa: BLE001
    report("audio", False, f"-> {e}")

print("\nAll required checks passed." if ok else "\nSome checks failed - see above.")
