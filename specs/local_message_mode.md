# Local Message Mode Specification

## Overview

Local Message Mode allows the agent simulation to run entirely without Slack — agents communicate through the local PostgreSQL database instead. All inter-agent conversations, channel history, and message delivery are handled via a `local_messages` table. The web app, podcast pipeline, profile pipeline, and all other subsystems are unaffected.

This mode is useful for:
- Developing and testing agent logic without Slack credentials or live bots
- Running offline/CI simulations against the full agent reasoning stack
- Replaying or resuming simulations that are fully self-contained in the database
- Evaluating agent output (proposals, LLM call logs) without a Slack workspace

It is activated by a single environment variable: `LOCAL_MODE=true`.

---

## Current Slack Dependency — What Changes

The simulation currently uses Slack for four things:

| Role | Current (Slack) | Local Mode Substitute |
|---|---|---|
| Message delivery | `chat.postMessage` | INSERT into `local_messages` |
| Channel history (startup rebuild) | `conversations.history` + `conversations.replies` | SELECT from `local_messages` |
| Incremental polling (each turn) | `conversations.history` cursor polling | SELECT from `local_messages` WHERE `ts > cursor` |
| PI input (DMs, channel mentions) | `conversations.history` on DM channels | Disabled (no human PIs in local mode) |

Everything else — Phase 2/4/5 LLM logic, Anthropic tool use, `agent_messages`, `llm_call_logs`, `thread_decisions`, `proposal_reviews` DB writes — is **unchanged**.

### What is NOT currently stored in the database

The main gap: **message content**. The existing `agent_messages` table stores only metadata (`agent_id`, `channel_id`, `message_ts`, `phase`, `message_length`) — not the actual message text. Message text lives only on Slack (and in-memory in `MessageLog`, which is rebuilt from Slack on every restart). Local mode closes this gap by adding a `local_messages` table that stores full content.

---

## Database: `local_messages` Table

New table added via Alembic migration `0019_add_local_messages.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `ts` | TEXT UNIQUE NOT NULL | Synthetic Slack-compatible timestamp (`datetime.utcnow().isoformat()`) — used as the canonical message ID throughout the system |
| `channel_id` | TEXT NOT NULL | Synthetic channel ID (e.g. `"local-general"`) |
| `channel_name` | TEXT NOT NULL | Human-readable channel name (e.g. `"general"`) |
| `sender_agent_id` | TEXT | Null for future human/PI messages |
| `sender_name` | TEXT NOT NULL | Bot display name |
| `content` | TEXT NOT NULL | Full message text |
| `thread_ts` | TEXT | Null = top-level post; otherwise = `ts` of the parent post |
| `is_bot` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `simulation_run_id` | INTEGER FK → `simulation_runs` | |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | |

Indexes: `(channel_id, ts)`, `(channel_id, thread_ts)`.

---

## New Component: `LocalMessageClient`

File: `src/agent/local_client.py`

A drop-in substitute for `AgentSlackClient` (`src/agent/slack_client.py`). Implements the same method signatures so `SimulationEngine` needs no changes to its agent-facing logic.

| `AgentSlackClient` method | `LocalMessageClient` behaviour |
|---|---|
| `connect()` / `auth_test()` | No-op; returns synthetic `{"user_id": agent_id, "user": bot_name}` |
| `post_message(channel_id, text, thread_ts)` | INSERT into `local_messages`; return `{"ts": synthetic_ts}` |
| `get_full_channel_history(channel_id)` | SELECT WHERE `channel_id = ? AND thread_ts IS NULL ORDER BY ts` |
| `get_all_thread_replies(channel_id, thread_ts)` | SELECT WHERE `channel_id = ? AND thread_ts = ? ORDER BY ts` |
| `poll_channel_messages(channel_id, oldest)` | SELECT WHERE `channel_id = ? AND ts > oldest ORDER BY ts` |
| `get_thread_replies(channel_id, thread_ts, oldest)` | SELECT WHERE `channel_id = ? AND thread_ts = ? AND ts > oldest ORDER BY ts` |
| `list_channels()` | Return static `{name: synthetic_id}` dict from seeded channel list |
| `create_channel(name)` | No-op; return synthetic channel ID |
| `join_channel(channel_id)` | No-op |
| `open_dm_channel(user_id)` | No-op; return dummy ID |
| `send_dm(channel_id, text)` | Log to stdout only |
| `resolve_user_name(user_id)` | Return `user_id` unchanged |
| `is_bot_user(user_id)` | Return `True` if `user_id` matches any known `agent_id` |

---

## Changes to `SimulationEngine` (`src/agent/simulation.py`)

### Client instantiation (`__init__`)

```python
if settings.local_mode:
    client = LocalMessageClient(agent_id, db_session, simulation_run_id)
else:
    client = AgentSlackClient(bot_token, app_token)
```

### `_rebuild_state()` (rename from `_rebuild_state_from_slack`)

Add a branch: if `local_mode`, populate `MessageLog` by querying `LocalMessageClient` (which reads from `local_messages`) using the exact same loop currently used for Slack. The `MessageLog.append()` call and all downstream state reconstruction (`active_threads`, `pending_proposals`, `_closed_thread_ids`) are unchanged.

### `_ensure_seeded_channels()`

If `local_mode`, skip `conversations.list` / `conversations.create` / `conversations.join`. Populate `_channel_id_map` from a static dict using synthetic IDs (e.g. `{"general": "local-general", ...}`).

### `_poll_slack_for_pi_messages()` and `_poll_pi_dms()`

Gate both behind `if not settings.local_mode`. In local mode there are no human PIs sending messages, so these polling loops are skipped entirely.

---

## Config (`src/config.py`)

```python
local_mode: bool = False  # Run simulation without Slack; all messages stored in local_messages table
```

`.env.example` addition:

```
LOCAL_MODE=false
```

---

## What Is Unchanged

- All Phase 2, 4, and 5 LLM reasoning and prompt logic
- Anthropic tool use in Phase 4 (`retrieve_profile`, `retrieve_abstract`, `retrieve_full_text`, `retrieve_foa`)
- All DB writes: `agent_messages`, `llm_call_logs`, `thread_decisions`, `proposal_reviews`, `pi_proposal_evaluations`
- `MessageLog` class interface (`src/agent/message_log.py`)
- `Agent`, `AgentState`, `PIHandler` classes (PI handling simply becomes a no-op)
- Web app, admin dashboard, podcast pipeline, worker, GrantBot

---

## Files to Create / Modify

| File | Action |
|---|---|
| `alembic/versions/0019_add_local_messages.py` | Create — new migration |
| `src/agent/local_client.py` | Create — `LocalMessageClient` class |
| `src/agent/simulation.py` | Modify — client injection, `_rebuild_state()` branch, gate PI polling, `_ensure_seeded_channels()` branch |
| `src/config.py` | Modify — add `local_mode: bool = False` |
| `.env.example` | Modify — add `LOCAL_MODE=false` |

---

## Verification

1. Set `LOCAL_MODE=true` in `.env`
2. Apply migration: `docker compose exec app alembic upgrade head`
3. Start simulation: `docker compose --profile agent run -d --name agent-run agent python -m src.agent.main --budget 10`
4. Confirm messages are written:
   ```sql
   SELECT sender_name, channel_name, LEFT(content, 80), thread_ts
   FROM local_messages ORDER BY created_at LIMIT 20;
   ```
5. Confirm proposals are recorded: `SELECT * FROM thread_decisions;`
6. Confirm LLM calls are logged: `SELECT agent_id, model, input_tokens FROM llm_call_logs LIMIT 10;`
7. Stop and restart the agent container; confirm simulation resumes correctly by reading history from `local_messages` without needing Slack
