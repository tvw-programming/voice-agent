# Installation

This guide takes you from a fresh machine to a running Vani voice agent. It covers macOS (primary), Linux, and Windows.

For what each setting does, see [CONFIGURATION.md](CONFIGURATION.md). For connecting your real student API and cloud services, see [INTEGRATION.md](INTEGRATION.md).

---

## 1. Prerequisites

| Requirement | Version / notes |
|---|---|
| Python | **3.10, 3.11 or 3.12** (3.13 is not yet supported by some audio/torch wheels) |
| LM Studio | Latest, from <https://lmstudio.ai>. Open it once after installing so the `lms` CLI can bootstrap |
| Disk space | About 12 GB total: roughly 5 GB for Qwen3 8B, 1.6 GB for Whisper large-v3-turbo, torch, and optional Parler (~4 GB) |
| RAM | 16 GB recommended (8 GB works with a smaller LLM, see CONFIGURATION.md) |
| espeak-ng | Needed by Kokoro for Hindi voices |
| git | Only if you install the optional Indic Parler-TTS voice |
| Headset | Strongly recommended. With open speakers the agent can hear itself and interrupt itself (barge-in) |

### Install system packages

**macOS (Homebrew)**

```bash
brew install python@3.12 espeak-ng portaudio git
```

**Ubuntu / Debian**

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv espeak-ng libportaudio2 libsndfile1 git
```

**Windows**

- Python 3.12 from <https://www.python.org/downloads/> (tick "Add python.exe to PATH")
- espeak-ng `.msi` from <https://github.com/espeak-ng/espeak-ng/releases>
- Git from <https://git-scm.com/download/win> (only for Parler)

---

## 2. Set up the local LLM (LM Studio)

1. Install LM Studio and open it once, then close it.
2. From the project folder, run:

   ```bash
   # macOS / Linux
   bash scripts/setup_lmstudio.sh
   ```

   ```powershell
   # Windows
   powershell -ExecutionPolicy Bypass -File scripts\setup_lmstudio.ps1
   ```

   The script downloads `qwen/qwen3-8b` (about 5 GB; choose **Q4_K_M** if asked), loads it with the identifier `qwen3-8b` and an 8192-token context, and starts the API server on port **1234**.

3. If it prints `'lms' CLI not found`, it installs the CLI for you. **Open a new terminal** and run the script again.

4. Confirm it works:

   ```bash
   curl http://localhost:1234/v1/models
   ```

   You should see `qwen3-8b` in the list.

> After a reboot, LM Studio's server is not running. Start it again with
> `lms load qwen/qwen3-8b --identifier qwen3-8b --context-length 8192 --gpu max && lms server start --port 1234`,
> or re-run the setup script (it skips the download if the model is already present).

---

## 3. Create the Python environment

```bash
# macOS / Linux
bash scripts/install.sh
source .venv/bin/activate
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
.\.venv\Scripts\Activate.ps1
```

This creates `.venv`, installs everything in `requirements.txt`, and copies `.env.example` to `.env`.

### Optional: Indic Parler-TTS (best local Indian voice)

```bash
pip install -r requirements-parler.txt
```

Parler sounds the most natural but is slow without an NVIDIA GPU. On a Mac, CPU synthesis can take several seconds per sentence. If you don't install it, the agent skips it automatically and uses Kokoro. On a Mac you may also want to move `local_kokoro` to the front of `tts.order` (see CONFIGURATION.md).

---

## 4. Fill in `.env`

Open `.env` and set at least:

```ini
STAFF_PIN=4321                              # PIN callers must say before student lookups
STUDENT_API_BASE_URL=http://127.0.0.1:8001  # mock API for now
STUDENT_API_KEY=mock-key
```

Cloud keys (`ANTHROPIC_API_KEY`, `SARVAM_API_KEY`) are optional. Without them the agent runs fully local, just with no cloud fallback. All variables are listed in CONFIGURATION.md.

---

## 5. Start the mock student API (for testing)

In a **second terminal** with the venv activated:

```bash
python mock_api/server.py
```

It serves five sample students on `http://127.0.0.1:8001` and accepts the key `mock-key`.

---

## 6. Run the pre-flight check

```bash
python scripts/check_setup.py
```

Every required line should show `[OK]`. `(optional) parler_tts` showing `--` is fine. The check covers:

- Python packages
- LM Studio reachable and `qwen3-8b` loaded
- Cloud keys present
- Student API URL, PIN and reachability
- Default microphone and speaker

---

## 7. Run the agent

```bash
export PYTHONPATH=src            # Windows: $env:PYTHONPATH="src"

python -m voice_agent.main --text          # typing only, no audio. Best first test
python -m voice_agent.main                 # full voice mode
python -m voice_agent.main --gender male   # male voice for this run
python -m voice_agent.main --config other.json
```

**The first voice run downloads Whisper, Silero VAD and Kokoro models** (and Parler if installed), so it can take a few minutes before the greeting plays.

### Quick test script (text mode, mock API)

```
You: hi, what can you do?
You: I want details of a student
You: my PIN is four three two one
You: Priya Kulkarni
You: yes
```

Vani should read back class 10, division B, roll number 27, attendance and last exam result. It should never read the phone number, address or date of birth.

Say or type `bye` to quit, or press Ctrl+C.

---

## 8. Run the tests

```bash
pytest
```

All 22 tests should pass. They don't need LM Studio, audio, or network access.

---

## 9. macOS microphone permission

The first time voice mode opens the mic, macOS asks whether **Terminal** (or iTerm / VS Code) may use the microphone. Allow it. If you denied it earlier, go to **System Settings → Privacy & Security → Microphone** and turn it on for your terminal app, then restart the terminal.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cp: .env.example: No such file` during install | Make sure `.env.example` exists in the project root (it is included), then re-run the script |
| `LM Studio server reachable -- Connection refused` | LM Studio server isn't running. Run `lms server start --port 1234` |
| `model 'qwen3-8b' available --` | The model is loaded under another identifier. Run `lms ps`, then either reload with `--identifier qwen3-8b` or change `llm.local.model` in `config.json` |
| `PortAudio library not found` | macOS: `brew install portaudio`. Linux: `sudo apt install libportaudio2` |
| Kokoro error mentioning `espeak` | Install espeak-ng (step 1) and open a new terminal |
| Agent keeps interrupting itself | Use a headset, or raise `audio.barge_in.threshold` to about 0.9, or set `enabled: false` |
| Very slow replies on Mac | Parler on CPU is slow. Put `local_kokoro` first in `tts.order`, or uninstall Parler |
| `torch` install fails on Python 3.13 | Recreate the venv with Python 3.12 |
| Student lookup says "service is not configured" | `STUDENT_API_BASE_URL` is empty in `.env` |
| Student lookup says "not responding" | Mock API isn't running, or the URL/key is wrong. `check_setup.py` shows which |
