# Zippy Agent — Complete Build Plan

End-to-end plan for building the Zippy chatbot inside Beacon CRM. Starts from what's already built (the Drive folder picker), ends with a working Copilot-style agent that searches Drive, answers questions, and generates documents.

---

## What's already built (starting point)

- `app/clients/google_drive.py` — Drive API v3 wrapper (list, search, get metadata, list files in folder)
- `app/api/v1/endpoints/drive.py` — 7 endpoints for folder browsing + selection
- `app/models/user_email_connection.py` — extended with `selected_drive_folder_id`, `selected_drive_folder_name`, `is_admin_folder`
- `alembic/versions/049_drive_folder_selection.py` — migration for the above
- `frontend/src/components/DriveFolderPicker.tsx` — modal folder browser
- Settings UI section for admin folder + personal folder selection

Gmail OAuth already requests `drive.readonly` scope, so any connected user has Drive access.

---

## Phase 1 — RAG foundations (knowledge layer)

Goal: index the selected Drive folder's files into a searchable vector store (Qdrant).

**Step 1.1 — Add Qdrant as a service**

In `docker-compose.yml`, add:

```yaml
qdrant:
  image: qdrant/qdrant:latest
  ports:
    - "6333:6333"   # REST API
    - "6334:6334"   # gRPC
  volumes:
    - qdrant_data:/qdrant/storage
  restart: unless-stopped
```

Add `qdrant_data:` to the `volumes:` block.

Add to `.env` / `.env.example`:
```
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=            # empty for local dev, set in prod
QDRANT_COLLECTION=beacon_knowledge
```

Add matching fields to `app/config.py`.

**Step 1.2 — Qdrant client wrapper**

Create `app/clients/qdrant_client.py` — thin wrapper around the official `qdrant-client` Python SDK. Exposes:

- `ensure_collection()` — creates the `beacon_knowledge` collection if missing, with 1536-dim cosine-distance vectors
- `upsert_chunks(points: list[PointStruct])` — batch upsert with payload
- `search(query_vector, filter_conditions, top_k=8)` — vector search with payload filters
- `delete_by_source_id(source_id)` — removes all points for a file
- `count(filter_conditions)` — for telemetry

Call `ensure_collection()` on app startup (from `app/main.py`'s startup event).

**Step 1.3 — Collection schema (Qdrant payload)**

Each point in Qdrant has a vector + a payload (JSON). The payload replaces what would have been a Postgres row:

```python
{
  "source_type": "drive",
  "source_id": "<drive_file_id>",
  "source_url": "<webViewLink>",
  "folder_id": "<selected_folder_id>",
  "owner_user_id": "<user_uuid>",
  "is_admin_scope": false,
  "file_name": "Q3 Playbook.docx",
  "chunk_index": 0,
  "chunk_text": "<the actual text>",
  "content_hash": "<sha256>",
  "mime_type": "application/vnd.google-apps.document",
  "modified_time": "2026-04-18T10:30:00Z",
  "indexed_at": "2026-04-19T12:00:00Z"
}
```

Create payload indexes (for fast filtering) on: `source_id`, `owner_user_id`, `is_admin_scope`, `folder_id`, `content_hash`. Set these up inside `ensure_collection()`.

Point IDs: use a deterministic UUID derived from `sha256(source_id + chunk_index)` so re-indexing cleanly overwrites.

No Postgres migration needed for this phase — Qdrant holds everything.

**Step 1.3 — Embedding client**

Create `app/clients/embeddings.py` — wrapper around OpenAI's `text-embedding-3-small` (1536 dims, $0.02/1M tokens). Expose `embed_texts(list[str]) -> list[list[float]]` with batching (100 chunks per API call).

Add `OPENAI_API_KEY` to `.env` and `.env.example`, and to `app/config.py`.

**Step 1.4 — Text extraction**

Create `app/services/text_extraction.py` with functions for each file type:

- Google Docs → Drive export as `text/plain`
- PDFs → `pdfplumber`
- `.docx` → `python-docx`
- `.xlsx` → `openpyxl`, extract each sheet as a text block
- `.txt`, `.md`, `.csv` → read as-is
- Images / binary → skip

**Step 1.5 — Chunker**

Create `app/services/chunker.py`. Sliding window chunker, ~500 tokens with 50-token overlap. Use `tiktoken` for token counting.

**Step 1.6 — Indexer**

Create `app/services/knowledge_indexer.py` with `index_file(file_id, connection, is_admin)`:

1. Fetch file bytes via Drive API
2. Compute SHA-256 of the bytes
3. Check Qdrant for any existing point with matching `source_id` — if the stored `content_hash` matches, skip (idempotent)
4. Extract text
5. Chunk into pieces
6. Embed all chunks in one batch call
7. `qdrant.delete_by_source_id(source_id)` then `qdrant.upsert_chunks(...)` with fresh points

Create `app/tasks/knowledge_indexer.py` (Celery task) wrapping the above and a `index_folder(connection_id)` that iterates over every file in the selected folder and calls `index_file`.

**Step 1.7 — Trigger indexing on folder selection**

In `app/api/v1/endpoints/drive.py`, when `POST /drive/folder/select` or `/folder/select-admin` saves a folder, queue `index_folder.delay(connection.id)` immediately.

---

## Phase 2 — Drive sync (incremental)

Goal: keep the knowledge base in sync as files are added/modified/removed in Drive.

**Step 2.1 — Add `drive_page_token` to UserEmailConnection**

Migration `051_drive_page_token.py` — adds a nullable `drive_page_token VARCHAR` column.

**Step 2.2 — Initial token**

After the first full indexing run completes, fetch Drive's current `startPageToken` (`GET /drive/v3/changes/startPageToken`) and save it to the connection.

**Step 2.3 — Sync task**

Create `app/tasks/drive_sync.py` with `sync_drive_folder(connection_id)`:

1. Call `GET /drive/v3/changes?pageToken=...` (paginate)
2. For each change, check if the file is inside the selected folder (walk up `parents` chain)
3. Dispatch to indexer:
   - Added/modified → `index_file`
   - Trashed/removed → `qdrant.delete_by_source_id(source_id)`
   - Renamed → re-run `index_file` (simplest — the payload gets refreshed with new `file_name`)
4. Save the new `pageToken`

**Step 2.4 — Celery Beat schedule**

Add to `app/celery_app.py`:

- `sync-drive-folders`: every 10 minutes, runs `enqueue_drive_syncs` which iterates every active connection with a selected folder and calls `sync_drive_folder.delay(conn.id)`

**Step 2.5 — Manual sync button**

In Settings → Drive section, add a "Sync now" button next to each folder card, hitting `POST /api/v1/drive/folder/sync`.

---

## Phase 3 — Drive as the single source of truth

Goal: every knowledge file lives in Drive. No parallel upload store. If a user wants to add a file to Zippy's knowledge, it goes into their selected Drive folder — either they put it there themselves, or the UI uploads it to Drive on their behalf.

**Step 3.1 — "Upload to Drive" endpoint**

`POST /api/v1/drive/upload` — accepts a multipart file. Flow:

1. Look up the current user's active `UserEmailConnection` + their `selected_drive_folder_id` (or admin folder depending on a `scope` query param)
2. Error if no folder is selected
3. Use Drive API `files.create` with `uploadType=multipart`, setting `parents=[folder_id]` so the file lands in the selected folder
4. Return `{ drive_file_id, webViewLink }`
5. Queue `index_file.delay(drive_file_id, connection_id)` to index it immediately instead of waiting for the 10-minute sync

**Step 3.2 — Wire the paperclip to this endpoint**

In the Zippy panel (Phase 7), the paperclip icon calls `POST /api/v1/drive/upload` — it does NOT hit a separate upload store. The file appears in Drive, gets indexed, becomes searchable. Same flow whether a user drops a PDF in Zippy or adds one to Drive manually.

**Step 3.3 — No separate deletion endpoint**

To remove a file from Zippy's knowledge, delete it in Drive. The incremental sync (Phase 2) picks up the trash event and removes it from Qdrant automatically.

**Why this matters**

Single source of truth: Drive. One sync pipeline to maintain. No orphaned uploads. Permissions already match what Google allows. Easier to reason about, easier to debug.

---

## Phase 4 — Zippy chat endpoint (basic RAG, no tools yet)

Goal: ship a working chat bot that answers from the knowledge base. No tool use yet — just retrieval + generation.

**Step 4.1 — Conversation models**

Migration `052_zippy_conversations.py`:

- `zippy_conversations(id, user_id, title, created_at, updated_at)`
- `zippy_messages(id, conversation_id, role, content, tool_calls JSONB, created_at)`

**Step 4.2 — Retrieval service**

Create `app/services/knowledge_search.py` with `search(query, user_id, top_k=8)`:

1. Embed the query
2. Call `qdrant.search(query_vector, filter=...)` with a Qdrant filter that says "payload.owner_user_id = user_id OR payload.is_admin_scope = true"
3. Return top-K points (chunk text + payload metadata + similarity score)

Qdrant filter shape:
```python
Filter(
    should=[
        FieldCondition(key="owner_user_id", match=MatchValue(value=user_id)),
        FieldCondition(key="is_admin_scope", match=MatchValue(value=True)),
    ]
)
```

**Step 4.3 — Chat endpoint**

Create `app/api/v1/endpoints/zippy.py`:

- `POST /zippy/conversations` — create new conversation
- `GET /zippy/conversations` — list user's conversations
- `GET /zippy/conversations/{id}/messages` — load history
- `POST /zippy/chat` — takes `{ conversation_id, message }`:
  1. Call `knowledge_search.search(message)` → top 8 chunks
  2. Build a Claude prompt: system = "You are Zippy, Beacon's internal assistant. Answer using only the provided context. Cite sources by file name." User message = original message + retrieved chunks
  3. Call Claude API via existing `app/clients/claude_enrichment.py` pattern
  4. Save user + assistant message to `zippy_messages`
  5. Return assistant response + the source chunks used

Ship this — it's already useful.

---

## Phase 5 — Agent tools (upgrade from RAG to real agent)

Goal: let Zippy call tools to search, fetch Drive links, and generate documents.

**Step 5.1 — Define tools**

In `app/services/zippy_tools.py`, define 5 tools (Claude tool-use format):

- `search_knowledge(query)` — wraps `knowledge_search.search`. Returns chunks.
- `get_drive_link(file_name)` — searches `knowledge_chunks` by file name, returns Drive `webViewLink`.
- `generate_mom(meeting_id)` — see Phase 6.
- `generate_nda(jurisdiction, client_name, signer_name, effective_date, other_fields)` — see Phase 6.
- `generate_document(title, markdown_content, format)` — generic generator, format = `docx | pdf`.

**Step 5.2 — Tool-use loop**

Rewrite `POST /zippy/chat` to do tool use:

```
while True:
    response = claude.messages.create(
        model="claude-sonnet-4",
        tools=ZIPPY_TOOLS,
        messages=conversation_history,
    )
    if response.stop_reason == "end_turn":
        break
    for tool_call in response.tool_calls:
        result = run_tool(tool_call.name, tool_call.input)
        conversation_history.append({"role": "tool", "content": result})
```

Persist each tool call to `zippy_messages.tool_calls` so the UI can show "🔍 Searched Drive…" pills.

**Step 5.3 — System prompt**

System prompt tells Zippy:
- Your name is Zippy, Beacon's internal assistant
- You have access to the user's Drive folder + uploaded files via `search_knowledge`
- Use `get_drive_link` when the user asks for a file or link
- For MOMs, NDAs, or document generation, call the right tool
- Always cite sources when answering from the knowledge base

---

## Phase 6 — Document generators

Three generators. Put them all under `app/services/doc_generators/`.

**Step 6.1 — MOM generator (`mom.py`)**

You already have the MOM skill (`mom-generator`) and template. Port that template to `templates/mom_template.docx` (use `docxtpl` placeholders like `{{ meeting_title }}`, `{{ attendees }}`, `{{ action_items }}`).

Function: `generate_mom(meeting_id, transcript_text) -> file_path`

1. Fetch meeting metadata
2. Pull transcript (from tldv or DB)
3. Call Claude to structure it into MOM sections (title, attendees, agenda, key decisions, action items, next steps)
4. Fill `mom_template.docx` with `docxtpl`
5. Save to `/uploads/generated/mom_{meeting_id}.docx`
6. Return `{ file_url, preview_url }`

**Step 6.2 — NDA generator (`nda.py`)**

Three templates (ask a lawyer to review these before using in production):

- `templates/nda_india.docx`
- `templates/nda_us.docx`
- `templates/nda_singapore.docx`

Each has placeholders: `{{ disclosing_party }}`, `{{ receiving_party }}`, `{{ effective_date }}`, `{{ jurisdiction }}`, `{{ term_years }}`, `{{ governing_law }}`.

Function: `generate_nda(jurisdiction, fields) -> file_path`

1. Validate `jurisdiction in ['india', 'us', 'singapore']`
2. Load corresponding template
3. Fill with `docxtpl`
4. Save to `/uploads/generated/nda_{jurisdiction}_{uuid}.docx`
5. Return `{ file_url }`

**Step 6.3 — Generic document generator (`generic.py`)**

Function: `generate_document(title, markdown_content, format='docx') -> file_path`

- `docx` path: `python-docx` converts markdown to styled Word doc
- `pdf` path: `weasyprint` renders markdown → HTML → PDF

---

## Phase 7 — Copilot-style frontend

Goal: slide-out right panel, always accessible.

**Step 7.1 — Types + API wrapper**

Add to `frontend/src/lib/api.ts`:

```typescript
export interface ZippyMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: { name: string; input: any; result?: any }[];
  created_at: string;
}

export const zippyApi = {
  listConversations: () => request(...),
  createConversation: () => request(...),
  getMessages: (id) => request(...),
  sendMessage: (conversationId, message) => request(...),
  uploadFile: (file) => request(...),  // multipart
};
```

**Step 7.2 — Global Zippy button**

Create `frontend/src/components/ZippyLauncher.tsx` — a floating "b" avatar button fixed bottom-right on every page. Clicking it toggles `ZippyPanel` open/closed.

Mount this in `App.tsx` so it appears on every route.

**Step 7.3 — ZippyPanel.tsx**

Right-side slide-out drawer (width ~420px, full height). Structure:

- **Header** — conversation title + "New chat" + close button
- **Message list** (scrollable)
  - User messages: right-aligned, light-blue bubble
  - Assistant messages: left-aligned, white bubble with "b" avatar
  - Tool calls rendered as pills: `🔍 Searching your Drive…` / `📄 Generated NDA for India`
  - Citations: at the end of assistant messages, render source chips that open Drive links
  - Document outputs: render as a card with filename + "Open" + "Download" buttons
- **Input** — textarea with send button, paperclip icon for file upload, enter to send

**Step 7.4 — Streaming (nice-to-have)**

Make `POST /zippy/chat` return Server-Sent Events. Frontend reads them with `EventSource` for a typing effect. Not required for v1.

---

## Phase 8 — Polish

- **Conversation history** — left-side list of past conversations inside the panel, clickable to resume
- **Regenerate button** — re-runs the last user message with a fresh context
- **Delete conversation** — self-explanatory
- **Rate limiting** — cap messages per user per hour
- **Cost telemetry** — log tokens in/out per message to a `zippy_usage` table
- **Error recovery** — if Claude fails, show "Zippy hit an error, try again" without losing the user's message
- **Empty state** — when the panel opens with no messages: show suggestion chips like "Generate MOM for yesterday's meeting", "Create US NDA", "What's in the Q3 playbook?"

---

## Stack summary

- **Vector DB**: Qdrant (runs as a Docker service alongside Postgres + Redis)
- **Embeddings**: OpenAI `text-embedding-3-small` (1536 dims, $0.02/1M tokens)
- **LLM**: Claude Sonnet via `ANTHROPIC_API_KEY`
- **Knowledge source**: Google Drive only (no parallel upload store)
- **Document generation**: `python-docx` + `docxtpl` for Word, `weasyprint` for PDF
- **Text extraction**: `pdfplumber`, `python-docx`, `openpyxl`, Drive export API
- **Task queue**: existing Celery + Redis
- **Frontend**: existing React + Tailwind, one new component tree

## New dependencies to add to `requirements.txt`

```
qdrant-client
openai
tiktoken
pdfplumber
python-docx
docxtpl
openpyxl
weasyprint
```

## New environment variables

```
OPENAI_API_KEY=sk-...
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
QDRANT_COLLECTION=beacon_knowledge
```

## New services in docker-compose

```yaml
qdrant:
  image: qdrant/qdrant:latest
  ports:
    - "6333:6333"
    - "6334:6334"
  volumes:
    - qdrant_data:/qdrant/storage
```

---

## Suggested timeline

- **Week 1** — Phase 1 (RAG foundations) + Phase 4 (basic chat, no tools). Ship a v0 that answers from Drive.
- **Week 2** — Phase 2 (Drive sync) + Phase 3 (uploads). Knowledge stays fresh.
- **Week 3** — Phase 5 (tools) + Phase 7 (Copilot UI). Actual agent experience.
- **Week 4** — Phase 6 (doc generators). MOM first, NDAs second, generic last.
- **Week 5** — Phase 8 (polish + streaming + telemetry).

## Build order rule of thumb

Never build the UI before the backend. Every phase has a curl-able endpoint before it has a button. This makes debugging far easier when something breaks.
