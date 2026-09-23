# Vani — Indian voice agent

A local-first voice assistant that can:

1. hold normal spoken conversations (English, Hindi, Marathi and Hinglish);
2. look up students by first name and surname from a fixed REST API and read the details aloud;
3. run on a local open-source LLM in **LM Studio** (Qwen3 8B), falling back automatically to Claude Haiku 4.5 and then Sarvam-M in the cloud;
4. speak with Indian male or female voices, configured in `config.json`.

```
Mic → Silero VAD → faster-whisper (→ Sarvam Saaras) → LLM router → tools → TTS router → Speaker
                                   LM Studio → Claude → Sarvam-M     Parler → Kokoro → Sarvam Bulbul
```

## Documentation

- [INSTALLATION.md](INSTALLATION.md): step-by-step setup for macOS, Linux and Windows, plus troubleshooting
- [CONFIGURATION.md](CONFIGURATION.md): every `.env` variable and `config.json` setting
- [INTEGRATION.md](INTEGRATION.md): connecting your real student API and cloud services, adding tools

## Lookup features

Each feature is one file in `src/voice_agent/features/`, with a matching test file in `tests/`:

| Feature | File | What it does |
|---|---|---|
| Filler line | `filler.py` | Says "Ek second, check kar rahi hoon." if a lookup takes over 350 ms |
| Class / division / roll search | `class_search.py` | "Aarav in 8-A" or "roll 12 of 8-A" |
| Spelling mode | `spelling.py` | "K U L K A R N I", "B for Bombay"; longer pauses allowed while spelling |
| Sound-based matching | `phonetic.py` | Desmukh finds Deshmukh; Patel vs Patil must be confirmed |
| Audit log | `audit_log.py` | Tamper-evident `data/audit.db`, kept 7 days; `export`, `verify`, `purge`, `tail` commands |
| Per-staff PINs | `staff_pins.py` | 6-digit hashed PINs in `data/staff.json`; PIN checked before the LLM sees it |
| Role-based access | `access_roles.py` | Office staff see every student; teachers only their own classes |

The student API is a small Express server in `student_api/` with 40 dummy students (`npm install && npm start`).

First-time staff setup:

```bash
python -c "import secrets; print(secrets.token_hex(32))"      # put in .env as STAFF_PIN_SECRET
python -m voice_agent.features.staff_pins add --name "Sunita Patil" --role clerk   # prints the PIN once
python -m voice_agent.features.staff_pins add --name "Meena Kale" --role teacher --classes 8-A,8-B
python -m voice_agent.features.audit_log tail                  # see recent PIN attempts and lookups
```

Until at least one staff member is added, the old shared `STAFF_PIN` keeps working.

## Quick start (Windows)

```powershell
# 1. Local LLM: install LM Studio (https://lmstudio.ai), open it once, then:
powershell -ExecutionPolicy Bypass -File scripts\setup_lmstudio.ps1

# 2. Python environment (Python 3.10–3.12)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#    then edit .env (STAFF_PIN_SECRET, student API URL) and add yourself:
python -m voice_agent.features.staff_pins add --name "Your Name" --role admin

# 3. Start the student API (Node.js 18+) in a second terminal
cd student_api; npm install; npm start

# 4. Check everything
python scripts\check_setup.py

# 5. Run
$env:PYTHONPATH="src"
python -m voice_agent.main --text        # type-only mode, no audio needed
python -m voice_agent.main               # full voice mode
python -m voice_agent.main --gender male
```

macOS/Linux: use the `.sh` versions of the scripts and `export PYTHONPATH=src`.

Use a **headset**: with open speakers the agent can hear itself and trigger barge-in. If you must use speakers, raise `audio.barge_in.threshold` or set `enabled` to false.

## Voices (`config.json → tts`)

| Provider | Female | Male | Runs |
|---|---|---|---|
| Indic Parler-TTS | text description (warm, friendly) | text description (calm, professional) | local; GPU recommended; `pip install -r requirements-parler.txt` |
| Kokoro-82M | `hf_alpha` (alt `hf_beta`) | `hm_omega` (alt `hm_psi`) | local, fast; needs `espeak-ng` |
| Sarvam Bulbul v3 | `ishita` (alt `priya`) | `ratan` (alt `shubh`) | cloud, `SARVAM_API_KEY` |

Set `tts.preferred_gender` to `"female"` or `"male"`, or use `--gender` or the `VOICE_GENDER` env var. `tts.order` controls which provider is tried first; any provider that isn't installed or fails is skipped automatically.

## Student lookup

The agent enforces these steps **in code**, not only in the prompt:

1. **Caller PIN**: each staff member has their own 6-digit PIN (`staff_pins.py`). It is checked in code before the LLM sees it; three wrong attempts lock the call, and 10 failures in 10 minutes pause lookups for everyone. Spoken digits such as "four eight two nine one three" or "char aath do..." both work.
2. **Confirmation**: the lookup is refused unless `confirmed_by_user` is true, and close-but-different names (Patel for Patil) need a second confirmation.
3. **Matching**: exact search, then surname-only, then same-sounding surname spellings (Desmukh → Deshmukh), ranked by sound as well as spelling. Callers can also spell the name, or give class, division and roll number.
4. **Field whitelist**: only `speakable_fields` ever reach the LLM; `never_speak_fields` are removed even if whitelisted by mistake.

To connect your real API, set `STUDENT_API_BASE_URL` and `STUDENT_API_KEY` in `.env`, then in `config.json` adjust `endpoint`, `query_params` and `field_map` so they map to your API's actual field names. The response can be a list or an object with `results`, `data`, `students`, `items` or `records`.

## Fallback behaviour

Each stage (STT, LLM, TTS) tries providers in order. A provider is skipped for 60 seconds after 3 consecutive failures, then retried. For the LLM, fallback triggers on a startup health-check failure, no first token within the timeout, a connection error or malformed tool arguments. If the local model fails *mid-sentence*, the agent apologises rather than restarting the answer on another model, so nothing is spoken twice.

## Tests

```
pytest            # 104 tests: core (22), one file per feature, and the agent against the Express API
cd student_api && npm test   # 9 API tests
```
