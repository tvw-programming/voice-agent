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

## Quick start (Windows)

```powershell
# 1. Local LLM: install LM Studio (https://lmstudio.ai), open it once, then:
powershell -ExecutionPolicy Bypass -File scripts\setup_lmstudio.ps1

# 2. Python environment (Python 3.10–3.12)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#    then edit .env with your keys

# 3. Test without real data: start the mock student API in a second terminal
python mock_api\server.py

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

1. **Caller PIN**: `verify_caller` must succeed first (the PIN is set in `STAFF_PIN`; three wrong attempts lock lookups for the call). Spoken digits such as "four three two one" or "char teen do ek" both work.
2. **Name confirmation**: the lookup is refused unless `name_confirmed_by_user` is true.
3. **Fuzzy matching**: if the exact search fails, it searches by surname and fuzzy-matches the first name, which handles speech-to-text errors like "Pria" for Priya.
4. **Field whitelist**: only `speakable_fields` ever reach the LLM; `never_speak_fields` are removed even if whitelisted by mistake.

To connect your real API, set `STUDENT_API_BASE_URL` and `STUDENT_API_KEY` in `.env`, then in `config.json` adjust `endpoint`, `query_params` and `field_map` so they map to your API's actual field names. The response can be a list or an object with `results`, `data`, `students`, `items` or `records`.

## Fallback behaviour

Each stage (STT, LLM, TTS) tries providers in order. A provider is skipped for 60 seconds after 3 consecutive failures, then retried. For the LLM, fallback triggers on a startup health-check failure, no first token within the timeout, a connection error or malformed tool arguments. If the local model fails *mid-sentence*, the agent apologises rather than restarting the answer on another model, so nothing is spoken twice.

## Tests

```
pytest            # 22 tests: text normalisation, breaker, student tool, LLM router, agent loop
```
