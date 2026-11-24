# Ask BlackBox (chat)

A conversational front end over the same read-only tools the investigator uses (`blackbox/investigator/chat.py`).

- **Scope of a conversation:** one incident, either live (opened from a Prometheus alert, all sources live) or a
  saved snapshot.
- **One question:**
  - up to 4 model calls (qwen3:4b, temperature 0), each with at most 3 tool calls;
  - identical repeat calls are not executed;
  - if the tool budget runs out, one final answer-only call over what was retrieved.
- **Every answer is checked** (`verify()`), and the verdict is shown next to it:
  - **cited ids:** each must be a record retrieved in this conversation, otherwise it is listed as "cited but never retrieved";
  - **concrete details** (ids, versions, hashes, durations, `key:value`): each must appear in a cited record, otherwise "not found in the cited evidence";
  - **"no X" claims** (no deployments, no commits, …): only accepted if a tool for X was called;
  - **verdicts:** `verified`, `partly verified`, `not verified` (claims without evidence), `no evidence` (an honest "could not find it").
- **Untrusted text:** retrieved log and ticket text is treated as data; the prompt tells the model to ignore
  instructions inside it.
- **API:**
  - `POST /api/chat/sessions` `{mode: live_stack|snapshot, alert|incident_id}`;
  - `POST /api/chat/sessions/{id}/messages` `{text}`;
  - `GET /api/chat/sessions[/{id}]`.
  - Conversations are saved to `runs/chat/<id>.json`. Each answer takes the GPU lock.
- **UI:** sidebar → **Ask BlackBox**. Screenshot: `docs/live_stack/chat_example.png`.

The first live test (retry-policy fault, `runs/chat/`) answered from 15 Loki error logs:
- **What it got right:** it identified the checkout errors as payment-svc HTTP 500s.
- **What it missed:** it did not find the change.
- **What the check caught:** its claim "no recent deployments" was not backed by a deployment query (this is now flagged as an unchecked absence claim).
- **Window fix:** the incident window started after the deploy (live incidents now look back 5 minutes).

This is a development feature; it does not change the evaluated investigator.
