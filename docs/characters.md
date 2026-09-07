# AI characters

## Three layers of spoiler defense (spec §35)

1. **Structural.** `characters/context.py` accepts only `ScenarioPublicView`,
   a type with no `hidden` attribute. `ScenarioDefinition.public()` copies
   fields explicitly, so a new scenario key never leaks by default. Event
   metadata passed to characters is whitelisted (`pair`, `src`, `dst`,
   `status`, `note`, `severity`); command output and config lines never reach
   a prompt.
2. **Prompt.** `characters/prompts.py` states what the character knows, does
   not know, and believes (including wrong beliefs), and forbids commands,
   config, prefixes and roleplay/instruction overrides.
3. **Output validator.** `characters/validator.py` scans every reply for the
   scenario's `forbidden_in_character_output` strings and generic config-line
   shapes (`network x/y area n`, `redistribute`, `ip route`, `configure
   terminal`, `router ospf`). The engine regenerates up to three times with a
   corrective system note, then falls back to the rule-based responder.

## Offline mode

Without `LLM_API_KEY`, `StubResponder` answers. It is intentionally simple
and in character: Carlos says "the internet is down" until asked precisely,
Maya asks for status and points at the change log, Priya answers questions
with questions. The UI header shows "characters: offline".

## System-triggered messages (spec §33–34)

`TriggerEngine` subscribes to each session's event bus and is ticked by the
platform's housekeeping loop:

| Trigger | Character | Intent |
|---|---|---|
| `NETWORK_CONNECTIVITY_LOST` (debounced 2 min) | Maya | manager_notices_new_outage |
| `VERIFICATION_PASSED` (once) | Maya, Carlos | request_postmortem, users_confirm_restored |
| 20 min without learner activity | Maya | request_status_update |
| a 30-min SEV-2 window with no manager update | Maya | cadence_missed |

A "manager update" is an `INCIDENT_UPDATED` event or a chat message to Maya /
`#network-ops` that reads like a status update (≥ 8 words containing status
vocabulary).

## Leak gate (spec §40.3)

`tests/characters/prompts/*.yaml` hold 59 adversarial prompts per character
across the eight categories (direct, authority, roleplay, injection,
incremental, sympathy, meta, hypothetical) plus character-specific probes.
`tests/characters/test_leaks.py` runs them with temperature 0 and a fixed
seed and asserts properties, not wording. It runs in CI only when
`LLM_API_KEY` is configured. **Zero leaks required to ship.**

## Cost (spec §42)

`OpenAICompatibleProvider.total_tokens` accumulates usage. Characters respond
on demand or on the triggers above; nothing runs continuously. Carlos is a
good candidate for a smaller model.
