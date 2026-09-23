# Configuration

Vani is configured in two places:

- **`.env`**: secrets and machine-specific values (API keys, URLs, PIN). Never commit this file.
- **`config.json`**: behaviour (models, voices, timeouts, which student fields may be spoken).

`.env` is loaded automatically from the project root. You can point to a different JSON file with `--config path/to/file.json`.

---

## 1. Environment variables (`.env`)

| Variable | Required? | Used for |
|---|---|---|
| `STAFF_PIN` | Yes, for student lookups | PIN the caller must say before any lookup. Digits only, e.g. `4321` |
| `STUDENT_API_BASE_URL` | Yes, for student lookups | Base URL of the student API, e.g. `https://school.example.in/api` |
| `STUDENT_API_KEY` | If your API needs auth | Sent as `Authorization: Bearer <key>` |
| `ANTHROPIC_API_KEY` | Optional | First cloud LLM fallback (Claude Haiku 4.5) |
| `SARVAM_API_KEY` | Optional | Second cloud LLM fallback (Sarvam-M), cloud STT (Saaras) and cloud TTS (Bulbul) |
| `VOICE_GENDER` | Optional | `male` or `female`. Overrides `tts.preferred_gender` |

Priority for voice gender: `--gender` flag, then `VOICE_GENDER`, then `config.json`.

The names of these variables are themselves configurable (every `*_env` key in `config.json`), so you can rename them if they clash with something else on your machine.

---

## 2. `config.json` reference

### `agent`

| Key | Default | Meaning |
|---|---|---|
| `name` | `"Vani"` | Agent name, shown in text mode |
| `greeting` | Namaste greeting | First thing spoken on start |
| `default_language` | `"en-IN"` | Language code sent to TTS for Latin-script text |
| `devanagari_language` | `"hi-IN"` | Language code used when a sentence contains Devanagari. Set `"mr-IN"` if replies are mostly Marathi |
| `system_prompt_file` | `"prompts/system.md"` | Personality and rules. Edit this to change behaviour |
| `max_history_messages` | `24` | Conversation memory. Lower it for faster small models |
| `max_tool_rounds` | `4` | Max tool calls per reply before giving up |
| `exit_phrases` | bye, goodbye, … | Saying one of these ends the session |

### `audio`

| Key | Default | Meaning |
|---|---|---|
| `input_sample_rate` | `16000` | Mic sample rate (Whisper and Silero expect 16 kHz; leave it) |
| `block_size` | `512` | Samples per mic block (Silero needs 512 at 16 kHz) |
| `max_utterance_seconds` | `20` | Cuts off very long speech |
| `vad.threshold` | `0.5` | Speech probability to count as talking. Raise in noisy rooms |
| `vad.min_silence_ms` | `600` | Silence that ends your turn. Raise if it cuts you off mid-thought; lower for snappier replies |
| `vad.min_speech_ms` | `250` | Ignores clicks and short noises |
| `vad.pre_roll_ms` | `300` | Audio kept before speech starts so the first syllable isn't lost |
| `barge_in.enabled` | `true` | Let you interrupt the agent while it's speaking |
| `barge_in.threshold` | `0.8` | Stricter than normal VAD to avoid self-triggering. Use 0.9 on speakers |
| `barge_in.min_speech_ms` | `250` | How long you must speak to interrupt |

### `stt` (speech-to-text)

Local Whisper is always tried first, then Sarvam cloud.

| Key | Default | Meaning |
|---|---|---|
| `local.model` | `"large-v3-turbo"` | Whisper model. `small` or `medium` for weaker machines |
| `local.device` | `"auto"` | `cuda` on NVIDIA; Apple Silicon runs on CPU |
| `local.compute_type` | `"default"` | `int8` for faster CPU inference |
| `local.language` | `"en"` | Set `"hi"` for mostly Hindi callers, or remove to auto-detect |
| `local.initial_prompt` | Indian names | **Add your school's common names here.** It noticeably improves name recognition |
| `cloud.model` | `"saaras:v3"` | Sarvam STT model |
| `cloud.language_code` | `"unknown"` | Auto-detect. Or `hi-IN`, `mr-IN`, `en-IN` |
| `timeout_ms` | `8000` | Per-attempt timeout |

`strategy` is informational only; the order is fixed as local then cloud.

### `llm`

**Local model (`llm.local`)**

| Key | Default | Meaning |
|---|---|---|
| `base_url` | `http://localhost:1234/v1` | LM Studio server. Also works with Ollama (`http://localhost:11434/v1`) or any OpenAI-compatible server |
| `model` | `"qwen3-8b"` | Must match the identifier in `lms ps` |
| `temperature` | `0.4` | Lower = more predictable |
| `max_tokens` | `400` | Keep short for spoken replies |
| `supports_tools` | `true` | Set `false` if your local model can't do function calling (student lookups then go to cloud) |
| `first_token_timeout_ms` | `4000` | If the local model hasn't started answering by then, fall back to cloud |
| `api_key_env` | *(unset)* | Optional, if your local server needs a key |

**Cloud fallbacks (`llm.cloud`)**, tried in list order:

1. `anthropic`: `claude-haiku-4-5-20251001`, key `ANTHROPIC_API_KEY`
2. `openai_compatible` named `sarvam`: `sarvam-m`, key `SARVAM_API_KEY`, `supports_tools: false`

A provider without its key is skipped. To add another OpenAI-compatible provider (OpenAI, Groq, OpenRouter…), append an entry:

```json
{
  "provider": "openai_compatible",
  "name": "groq",
  "base_url": "https://api.groq.com/openai/v1",
  "model": "llama-3.3-70b-versatile",
  "api_key_env": "GROQ_API_KEY",
  "supports_tools": true
}
```

**Fallback rules (`llm.fallback`)**

| Key | Default | Meaning |
|---|---|---|
| `health_check_on_start` | `true` | Pings every provider at startup and skips dead ones immediately |
| `first_token_timeout_ms` | `2500` | Default for providers without their own value |
| `total_timeout_ms` | `20000` | Hard cap per reply |
| `circuit_breaker.failure_threshold` | `3` | Failures in a row before a provider is skipped |
| `circuit_breaker.cooldown_seconds` | `60` | How long it's skipped before retrying |

**Choosing a different local model**

| Machine | Suggested model (`lms get …`) | `model` value |
|---|---|---|
| 8 GB RAM | `qwen/qwen3-4b` | `qwen3-4b` |
| 16 GB RAM (default) | `qwen/qwen3-8b` | `qwen3-8b` |
| 32 GB+ / strong GPU | `qwen/qwen3-14b` | `qwen3-14b` |

Load it with `--identifier <model value>` so the names match.

### `tts` (voices)

| Key | Meaning |
|---|---|
| `preferred_gender` | `"female"` or `"male"` |
| `order` | Provider order. The first one that loads is used; the rest are fallbacks |
| `timeout_ms` | Per-sentence synthesis timeout |

**Voice providers (`tts.voices`)**

| Provider key | Female | Male | Notes |
|---|---|---|---|
| `local_indic_parler` | text `description` | text `description` | Voice is described in plain English. Edit the description to change tone, pace or age |
| `local_kokoro` | `hf_alpha` (alt `hf_beta`) | `hm_omega` (alt `hm_psi`) | `lang_code: "h"` = Hindi voice set, which also reads English with an Indian accent. `speed` 0.8–1.2 |
| `cloud_sarvam` | `ishita` (alt `priya`) | `ratan` (alt `shubh`) | `pace`, `temperature`, `sample_rate` |

To try an alternate voice, copy its `alternate` value into `voice_id`.

**Recommended `order` by machine**

| Machine | `order` |
|---|---|
| NVIDIA GPU | `["local_indic_parler", "local_kokoro", "cloud_sarvam"]` (default) |
| Mac / CPU only | `["local_kokoro", "cloud_sarvam", "local_indic_parler"]` |
| Best quality, internet OK | `["cloud_sarvam", "local_kokoro"]` |

### `tools.student_lookup`

Covered in detail in [INTEGRATION.md](INTEGRATION.md). Summary:

| Key | Meaning |
|---|---|
| `enabled` | Turn the tool off entirely (agent becomes chat-only) |
| `endpoint`, `method`, `query_params` | How to call your API |
| `field_map` | Maps Vani's field names to your API's field names |
| `speakable_fields` | The only fields the LLM ever sees |
| `never_speak_fields` | Always stripped, even if listed as speakable |
| `fallback_search_by_last_name` | Retry by surname only if the exact search finds nothing |
| `fuzzy_match.min_score` | 0–1. Lower (e.g. 0.8) tolerates more speech-recognition errors |
| `require_name_confirmation` | Agent must read the name back and get a yes |
| `max_matches_to_read` | If more match, the agent asks for class and division |
| `caller_auth_required`, `staff_pin_env`, `max_pin_attempts` | PIN gate |
| `timeout_ms`, `retries`, `cache_ttl_seconds` | Network behaviour |

### `logging`

| Key | Default | Meaning |
|---|---|---|
| `level` | `"INFO"` | `DEBUG` for troubleshooting |
| `log_transcripts` | `false` | Logs what callers and the agent say. Keep off in production, as it may include student data |

---

## 3. Common recipes

**Male voice by default**

```json
"tts": { "preferred_gender": "male", ... }
```

**Fully offline (no cloud at all)**: leave `ANTHROPIC_API_KEY` and `SARVAM_API_KEY` empty. Cloud providers are skipped automatically.

**Cloud only (no LM Studio)**: set `"health_check_on_start": true` (default). The local model fails the startup check and is skipped for 60 seconds at a time. For a permanent setup, point `llm.local` at your cloud endpoint instead.

**Mostly Marathi callers**

```json
"agent": { "devanagari_language": "mr-IN", ... },
"stt":   { "local": { "language": "mr", ... }, "cloud": { "language_code": "mr-IN", ... } }
```

**Disable the PIN (testing only)**

```json
"tools": { "student_lookup": { "caller_auth_required": false, ... } }
```

After any change, run `python scripts/check_setup.py` and `pytest` to confirm nothing broke.
