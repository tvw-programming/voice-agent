# Integration

How to connect Vani to your real student API and to the cloud fallback services, and how the pieces fit together.

Prerequisite: the agent already runs against the student API in `student_api/` (see [INSTALLATION.md](INSTALLATION.md)).

## 0. The student API (`student_api/`)

A small Express server with 40 static dummy students (`students.js`). Its parameter names are the ones the agent's default config expects, so no mapping is needed.

| Method and path | Purpose |
|---|---|
| `GET /health` | No key needed. `{"status":"ok","students":40}` |
| `GET /api/v1/students/search` | Search. Parameters below; at least one is required |
| `GET /api/v1/students/:id` | One student: `{"data": {...}}` or 404 |

Search parameters (all optional, all must match, names ignore case):

| Parameter | Example | Notes |
|---|---|---|
| `first_name` | `Priya` | Exact match |
| `last_name` | `Kulkarni` | Exact match; the agent handles misheard names by retrying same-sounding spellings |
| `class` | `8`, `10`, `UKG` | The agent normalises "eighth" or "VIII" to `8` before calling |
| `division` | `A` | |
| `roll_number` | `12` | Digits only, otherwise 400 |

Every `/api` request needs `Authorization: Bearer <key>`; the key is `STUDENT_API_KEY` when the server starts (default `dev-key`). Responses are `{"results": [...], "count": n}`, or `{"error": "..."}` with 400, 401 or 404.

Each record has `id`, `first_name`, `last_name`, `class`, `division`, `roll_number`, `attendance_percent` and `last_exam_result`. It also has `phone`, `address`, `date_of_birth` and `parent_contact`, on purpose, so you can see that the agent never speaks them.

To change the data, edit `student_api/students.js` and restart. To connect a different API later, follow section 2.

---

## 1. Architecture

```
Mic ─► Silero VAD ─► STT router ─► Agent ─► LLM router ─► tools ─► TTS router ─► Speaker
                     │                      │               │         │
                     ├ faster-whisper       ├ LM Studio     ├ verify_caller (PIN)
                     └ Sarvam Saaras        ├ Claude Haiku  └ get_student_details ─► Student API
                                            └ Sarvam-M                 ┌ Indic Parler
                                                                       ├ Kokoro
                                                                       └ Sarvam Bulbul
```

| File | Role |
|---|---|
| `src/voice_agent/main.py` | Entry point; voice and text loops, barge-in |
| `src/voice_agent/agent.py` | Conversation history and tool-calling loop; streams sentence by sentence |
| `src/voice_agent/llm.py` | LLM providers and fallback router |
| `src/voice_agent/stt.py` / `tts.py` | Speech in / speech out with fallback |
| `src/voice_agent/tools/registry.py` | Tool list, PIN session |
| `src/voice_agent/tools/student.py` | Student API client, fuzzy matching, field filtering |
| `prompts/system.md` | Agent personality and conversation rules |

---

## 2. Connecting your student API

### Step 1: Know your API

Collect these details from whoever owns the API:

- Base URL, e.g. `https://erp.myschool.in/api/v1`
- Search endpoint and method, e.g. `GET /students/search`
- Query parameter names for first name and surname
- Auth method (Vani sends `Authorization: Bearer <key>`)
- A sample JSON response

### Step 2: Set `.env`

```ini
STUDENT_API_BASE_URL=https://erp.myschool.in/api/v1
STUDENT_API_KEY=your-real-key
STAFF_PIN=choose-a-pin
```

### Step 3: Map the request in `config.json`

Suppose your API is `GET /v1/pupils?fname=Priya&sname=Kulkarni`:

```json
"endpoint": "/pupils",
"method": "GET",
"query_params": { "fname": "{first_name}", "sname": "{last_name}" }
```

`{first_name}` and `{last_name}` are replaced with what the caller said. Parameters that end up empty are dropped, which is how the surname-only fallback search works.

For a `POST` API, set `"method": "POST"`. Note that parameters are still sent as a query string; if your API needs a JSON body, see *Custom request shapes* below.

### Step 4: Map the response fields

Vani accepts any of these response shapes:

```jsonc
[ {...}, {...} ]                      // plain list
{ "results": [ ... ] }                // or data / students / items / records
{ "data": { ...single student... } }  // single object
```

Then map Vani's field names (left) to your API's field names (right). Suppose one record looks like this:

```json
{ "FirstName": "Priya", "Surname": "Kulkarni", "Std": "10", "Div": "B",
  "RollNo": 27, "AttendancePct": 97.2, "LastResult": "Distinction",
  "MobileNo": "98…", "Address": "…" }
```

Then:

```json
"field_map": {
  "first_name": "FirstName",
  "last_name": "Surname",
  "class": "Std",
  "division": "Div",
  "roll_number": "RollNo",
  "attendance_percent": "AttendancePct",
  "last_exam_result": "LastResult"
}
```

`first_name` and `last_name` **must** be mapped; they're used for matching.

### Step 5: Decide what may be spoken

```json
"speakable_fields": ["class", "division", "roll_number", "attendance_percent", "last_exam_result"],
"never_speak_fields": ["MobileNo", "Address", "phone", "address", "aadhaar", "date_of_birth", "parent_contact", "email"]
```

- Only `speakable_fields` are passed to the LLM. Everything else in the API response never leaves `student.py`.
- `never_speak_fields` is a safety net: anything listed there is removed even if someone adds it to `speakable_fields` by mistake. Add your API's own names for sensitive fields.
- To speak a new field (say `class_teacher`), add it to `field_map` **and** `speakable_fields`.

### Step 6: Test

```bash
python scripts/check_setup.py          # "student API reachable [OK]"
python -m voice_agent.main --text      # PIN → name → confirm
```

### Custom request shapes

If your API needs something the config can't express (a JSON body, an OAuth token refresh, a different auth header such as `X-API-Key`), edit `_search()` in `src/voice_agent/tools/student.py`. It only has to return a list of dicts; ranking, filtering and caching work unchanged. Add a test to `tests/test_student.py` for the new shape.

### How a lookup behaves

The tool returns a `status` that the LLM turns into speech:

| Status | When | What the agent does |
|---|---|---|
| `caller_not_verified` | No valid PIN yet | Asks for the staff PIN |
| `wrong_pin` / `locked` | Bad PIN / 3 bad PINs | Asks again / refuses lookups for the rest of the call |
| `missing_name` | Only one name given | Asks for the other |
| `needs_confirmation` | Name not yet confirmed | Reads the name back and asks "is that right?" |
| `found` | One match | Reads the speakable fields |
| `multiple_matches` | 2–3 matches | Asks which one, using class and division |
| `too_many_matches` | More than `max_matches_to_read` | Asks for class and division first |
| `not_found` | No match | Says so and offers to take the surname letter by letter |
| `confirm_match` | A close but different-sounding name (Patel for Patil) | Asks "Is that the student?" before sharing details |
| `need_more_info` | e.g. class without division | Asks for what's missing |
| `spelling_unclear` | Spelled letters couldn't be read | Asks the caller to spell again ("B for Bombay") |
| `not_in_your_classes` | A teacher asked about a class that isn't theirs | Explains that teachers can look up only their own classes, without saying whether the student exists |
| `limit_reached` | More than `max_lookups_per_call` lookups | Asks the caller to call again later |
| `error` | API down, 4xx, not configured | Apologises; details go to the log |

Results are cached per name for `cache_ttl_seconds` (default 5 minutes).

---

## 3. Cloud services

### Anthropic (Claude Haiku 4.5), LLM fallback #1

1. Create a key at <https://console.anthropic.com> → API Keys.
2. Add `ANTHROPIC_API_KEY=sk-ant-…` to `.env`.
3. Supports tool calling, so student lookups keep working when the local model is down.

### Sarvam AI: LLM fallback #2, cloud STT and cloud TTS

1. Create a key at <https://dashboard.sarvam.ai>.
2. Add `SARVAM_API_KEY=…` to `.env`.
3. One key enables three things:
   - **Sarvam-M** as the last LLM fallback. `supports_tools` is `false`, so it can chat but **cannot perform student lookups**.
   - **Saaras v3** STT when local Whisper fails.
   - **Bulbul v3** TTS voices (`ishita`, `priya`, `ratan`, `shubh`).

### When is each fallback used?

| Stage | Falls back when… |
|---|---|
| LLM | Startup health check fails; no first token within the timeout; connection error; malformed tool arguments |
| STT | Whisper fails to load or errors/times out on an utterance |
| TTS | Provider not installed, fails to load, errors or times out |

A provider that fails 3 times in a row is skipped for a cooldown (60 s for LLM and STT, 120 s for TTS), then retried. If the LLM fails **mid-sentence**, Vani apologises instead of restarting on another model, so nothing is spoken twice.

Run with `--text` and watch the `[provider]` tag after each reply to see which LLM answered.

---

## 4. Using other local LLM servers

Any OpenAI-compatible server works in `llm.local`:

| Server | `base_url` | Notes |
|---|---|---|
| LM Studio (default) | `http://localhost:1234/v1` | |
| Ollama | `http://localhost:11434/v1` | `model` = Ollama tag, e.g. `qwen3:8b` |
| llama.cpp server | `http://localhost:8080/v1` | Start with `--jinja` for tool calling |
| vLLM | `http://localhost:8000/v1` | Start with `--enable-auto-tool-choice` |

If the model doesn't support tool calling, set `"supports_tools": false`; lookups will then be handled by Claude.

`<think>…</think>` reasoning blocks (Qwen3, DeepSeek-R1) are stripped automatically so they are never spoken.

---

## 5. Adding a new tool

1. Create `src/voice_agent/tools/my_tool.py` with an OpenAI-style `SCHEMA` dict and an `async` function that returns a dict with a `status` key.
2. In `tools/registry.py`, append the schema in `schemas()` and dispatch it in `call()`.
3. Add its settings under `tools` in `config.json`.
4. Describe when to use it in `prompts/system.md`.
5. Add a test in `tests/`.

Keep tool results small and free of sensitive data. Everything a tool returns is sent to the LLM, which may be a cloud provider.

---

## 6. Production checklist

- [ ] `STAFF_PIN_SECRET` set to a long random value; every staff member added with `staff_pins add`, and the shared `STAFF_PIN` removed from `.env`
- [ ] Teachers have role `teacher` and the right classes (`staff_pins list`)
- [ ] `audit.retention_days` agreed with the school (default 7) and `audit_log verify` reports the chain intact
- [ ] `never_speak_fields` includes your API's own names for phone, address, Aadhaar, DOB and parent contact
- [ ] `logging.log_transcripts` is `false`
- [ ] `.env` is not in version control (add it to `.gitignore`)
- [ ] Student API key is read-only and scoped to search
- [ ] Decide whether student data may go to cloud LLMs (currently allowed: the data is dummy). To keep it local, remove the `anthropic` and `sarvam` entries from `llm.cloud`. The audit log's `llm_provider` column shows which model handled each lookup
- [ ] `pytest` passes and `check_setup.py` is all `[OK]`
