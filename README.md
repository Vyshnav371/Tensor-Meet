# Tensor: the live AI participant for meetings

Tensor joins a meeting as a participant. It listens the whole time but stays quiet until someone says **"Hey Tensor"**. It can then:

- answer from company knowledge, live product metrics, or a PDF just dropped into the chat;
- turn the discussion into Jira tickets or an email.

Tensor behaves like a well-mannered colleague:
- It keeps spoken answers short.
- Long answers go to the chat.
- It raises its hand instead of talking over people.
- It never sends or creates anything until a human approves it.

The original product specification is in [PRD.md](PRD.md).

---

## 1. The problem

Meetings produce work, but most of that work happens after the meeting:
- someone cross-checks the PDF a vendor sent;
- someone looks up last week's numbers;
- someone writes the Jira tickets;
- someone emails the stakeholders.

Meeting bots such as Otter or Fireflies record and summarise, which only turns the meeting into a to-do list. Voice assistants can act, but they don't work in a group: they talk over people, give 60-second answers, and take actions nobody checked.

Tensor closes this gap. It brings the context into the meeting and does the follow-up work during the meeting, with the people in the room approving each action.

## 2. Who it is for

| Person | Today | With Tensor |
|---|---|---|
| Engineering manager or tech lead | Spends 45 minutes after a meeting writing tickets and emails | Says *"Hey Tensor, turn those conflicts into Jira tickets for the security sprint and assign them to David"*, reviews the card on screen, and says *"go ahead"* |
| Product or operations lead | Leaves the call to search the wiki or check a PDF | Drops the vendor PDF into the chat and asks *"does this conflict with our security guidelines?"* |
| Any participant | Sits through AI voices that talk too long | Sees Tensor's raised hand and chooses: let it speak, or read the answer in the chat |

## 3. What Tensor can do

### Listen and wake up
- Every participant's microphone is transcribed live, with each line labelled by speaker. The transcript keeps a rolling 15-minute window, and the meeting room shows this as a consent pill.
- Tensor wakes on **"Hey Tensor"** or a directly addressed **"Tensor"**. Punctuation such as "Hey, tensor." is accepted, along with common speech-recognition variants including "Tenser", "Tensar", "Tenzor", "ten sir", and the greeting "hey dancer". Typing **`@Tensor`** in the chat does the same thing in text.
- Say only the wake phrase and Tensor acknowledges **"I'm here. Go ahead."**, then waits up to **20 seconds** for that speaker's question. Repeating the name refreshes the window without repeating the greeting. The acknowledgement also appears in chat; spoken feedback waits for a quiet room and never plays late over a question or answer.
- After it answers, Tensor keeps listening for 2 minutes for follow-ups, so you can say *"change the first one to highest priority"* or *"send it"* without repeating the wake word.
- **Tensor finishes its sentence.** People talking over it don't cut it off. Saying *"stop"*, *"Tensor, stop"*, *"hold on"* or *"that's enough"* does, while a longer sentence that merely contains "stop" doesn't.
- **It waits for you to finish.** Speech recognition splits a long request at every pause, so Tensor joins the pieces and acts once the speaker has been quiet for 1.5 seconds.
- **Two recognizers per microphone.**
  - **Chirp 3** writes the transcript and hears the commands. It's boosted with each company's own names and meeting vocabulary.
  - A **fast model** (`latest_long`) drives the live captions and the instant "listening" reaction.
  - On noisy Indian- and US-English test speech, Chirp 3 got 0.5% of words wrong, against 4.6% for the fast model alone.
  - If Chirp 3 fails, the fast model's text is used.
- **Tensor knows the meeting itself.** It knows who is here, who has left, the speaker-labelled transcript and the last 30 chat messages. So it answers "who's in the meeting?", "what did Sarah post in the chat?" or "recap the decisions so far" directly.
- **Waking up is silent.** When Tensor hears its name, it doesn't speak or post anything. It plays a soft chime (if Tensor's voice is on in that browser), ripples its orb and shows "I'm listening…", then waits for your request.
- **Tensor's voice is streamed.** Its first words play about a second after the answer is ready, while the rest is still being generated. Admins choose the voice on the Company page, with previews:
  - Gemini voices (default: Achird) sound most natural;
  - Chirp 3 HD voices start fastest.
- **Meeting minutes and documents.** Say *"Hey Tensor, make the meeting minutes"* (or *"write a doc about…"*), or click **Meeting minutes** in the Action center.
  - The writer agent builds the document from the meeting: people, transcript, chat, tickets and emails. For other documents it can also use company documents and data.
  - The result appears as a card in the Action center, and in the chat, with **Word (.docx)** and **Markdown** downloads.
  - Minutes have fixed sections: summary, discussion, decisions, an action-item table with owners and ticket keys, and open questions.
  - When an admin ends a meeting, Tensor writes the final minutes before the transcript is deleted, and offers them on the "meeting ended" screen.
- **Meeting memory.** Before transcript lines leave the 15-minute window, Tensor turns them into a short summary with speaker names. It also keeps per-person participation counts and a log of earlier requests and answers, so follow-ups like *"what did I ask you earlier?"* or *"same for last month"* work. All of this is deleted when the meeting ends.
- **Contribution summaries, with evidence.** *"Summarize what each attendee has contributed"* gives each person's speaking turns, points made, chat messages, files shared, and the tickets and actions they own (this meeting and earlier ones). Tensor does not rate anyone's "potential" or rank colleagues, because meeting data can't support that.
- **Emails keep their shape.** Every email has a greeting, a closing with the requester's name, and the "Sent via Tensor" line, enforced in code. Edits like *"make it more formal"* start from the full current draft and change only what was asked.
- **One system prompt for every agent:** shared rules on accuracy, meeting manners, memory, fairness about people, and untrusted documents.
- **Tensor knows what was done.** It knows every ticket (key and title), email and document in the Action center. It can also look up actions from earlier meetings in the audit log, so *"what was the last ticket?"* or *"what is SEC-404 about?"* get real answers.
- **Answers stream.** Tensor starts speaking as soon as its one-line summary is written and its numbers have been checked. The detailed answer then fills into the chat as it's written.
- **Admins can end a meeting for everyone.** This stops Tensor and any answer in progress, cancels open drafts, deletes the live transcript and disconnects everyone. Chat stays for reference. Joining the same meeting ID later starts it again.
- **Participants hear each other.** Voice goes directly between browsers (WebRTC, one connection per pair), not through the server. Each tile shows whether that person's mic is on and whether they're speaking. Because the audio arrives over WebRTC, Chrome's echo canceller removes other people's voices from your mic, so Tensor's transcript keeps every speaker separate.

### Answer from company knowledge (Knowledge agent)
- Searches **your company's** documents: policies, handbooks, SLAs, ADRs, runbooks, the team directory. Only your company's documents are ever searched.
- Every answer cites its sources, with clickable links in the chat.
- Answers numeric questions from **your company's BigQuery dataset**, such as "how did conversion change over the last two weeks?" or "which hub was worst on the worst day?". Tensor writes the SQL itself, and the server runs it only after these checks:
  - it is a single read-only `SELECT`;
  - a dry run confirms it reads only that company's tables;
  - it stays under a 100 MB scan budget.
  
  System tables and multi-statement scripts are refused.
- Checks every number Tensor says or posts against the tool results. A number that doesn't match gets a ⚠ warning instead of being stated as fact.

### Understand PDFs shared in the meeting (Document agent)
- Drag a PDF into the chat. Document AI parses it within seconds, including its tables.
- Ask questions about it, or ask Tensor to check it against company policy. Tensor builds a comparison table: each item in the spec, what the policy requires, and whether it conflicts, with the policy section cited.

### Plan work in Jira (Jira agent)
- Turns the discussion, or the conflicts Tensor found, into a ticket plan. Each ticket has a title, description, acceptance criteria, story points, priority and an assignee from the team directory.
- The plan appears as a **review card** on everyone's screen. You can change it in two ways:
  - on screen: include or exclude tickets, change the assignee, priority or points;
  - by voice: *"make the second one 5 points and give it to Rahul"*.
- Tickets are created only after approval, and the card then shows the new ticket keys.

### Write emails (Email agent)
- Drafts an email from the meeting context. Recipients come only from the company directory, and the email can include the ticket keys just created.
- The draft appears as a **review card**, where To, CC, subject and body are all editable. You can also edit by voice, for example *"CC Sarah"* or *"make it more formal"*.
- The email is sent only after *"send it"* or a click on Approve.

### Meeting etiquette
Before it says anything, Tensor decides how to deliver the answer:

| Situation | What Tensor does |
|---|---|
| Short answer, room is quiet | Says it out loud (always under 15 seconds) |
| Long or detailed answer (tables, lists, comparisons) | Says a one-line summary, and posts the full answer to the chat |
| Answer ready while people are still talking | **Raises its hand ✋** with a one-line topic ("Found 5 conflicts in the spec."). Anyone can say *"Go ahead, Tensor"*, click **Let Tensor speak**, or click **Read in chat** |
| An action is requested (tickets, email) | Shows a draft card and says only: *"I've put the draft on screen. Let me know if I should go ahead."* |

### Humans approve every action
Each draft card moves through a fixed set of statuses, in order:

```
PENDING_REVIEW → MODIFIED (after edits) → APPROVED → EXECUTING → EXECUTED / FAILED
                                     └──→ CANCELLED
```

- **Approval is never decided by the AI.** Only a fixed list of phrases spoken by a participant ("go ahead", "send it", "approve", "do it", …) or the Approve button can approve a card.
- **Each card runs once.** The step from APPROVED to EXECUTING is a database transaction, so a card cannot run twice, even if two people approve it at the same moment.
- **Every executed action is logged** to a BigQuery audit table: who approved it, how (voice or click), what was created, and when.

### Companies, sign-in and invites
- **Register a company** with your name, email, password, company name and industry. The industry is one of fintech, e-commerce, SaaS or logistics. You become the company's admin.
- In about 20 seconds Tensor sets the company up:
  - its own **BigQuery dataset** with 120 days of realistic sample data for that industry, including one planted incident to investigate;
  - a **starter handbook** with service targets, incident severity, security and retention rules, the team and the data dictionary, indexed in Vertex AI Search;
  - a fictional template **team** for drafting tickets and emails. Their addresses use the reserved `.example` domain, so nothing can ever be delivered to them.
- **Invite your team.** The admin copies an invite link from the Company page. Anyone who opens it creates an account and joins that company. A new link can be generated at any time, which disables the old one.
- **Add knowledge.** Admins upload PDF, HTML, TXT or DOCX documents. Each document becomes searchable for that company a few minutes after indexing.
- **Everything is per company.** Meetings, chat, shared files, cards, documents and data never cross companies. Two companies can both hold a meeting called `standup` and never see each other's.
- **Accounts:**
  - passwords are hashed with scrypt;
  - sessions are random tokens held server-side, in an HttpOnly, SameSite cookie, so page scripts can never read them;
  - repeated failed sign-ins are slowed down;
  - state-changing requests from other sites are refused.
- **The demo company, Acme Fintech**, has the original demo's policies, metrics and team. Its invite link is printed by `infra/seed_demo_company.py`, and the first person to join becomes its admin.

## 4. The meeting room

Sign in, pick a meeting ID on the start page, and share it with colleagues in your company. The **Company** page shows the invite link, the members, your BigQuery tables (with their columns) and your knowledge documents.

- **Join screen:** a name and meeting ID, with clear microphone consent. Shared links preserve the meeting ID when a new participant enters their name.
- **Meeting room:** participant tiles, an animated Tensor orb showing its state, hand-raise controls, captions, and a recent transcript. Pending drafts have a prominent review shortcut.
- **Workspace navigation:** dedicated views for the full **Live transcript**, **Action center**, and **Shared files**.
- **Action center:** editable Jira and email drafts, with Approve and Cancel buttons, explicit sandbox labels, and execution results. Nothing executes without approval.
- **Live transcript:** speaker-labelled lines. Text still being recognised appears greyed out.
- **Chat:** persistent alongside every workspace view, with markdown tables and source links, a full-answer dialog, quick prompts, PDF upload by picker or drag-and-drop, and the composer.
- **Shared files:** document parsing states and page/table counts, with PDF uploads up to 15 MB.
- **Controls:** mic on and off, Tensor voice on and off (turn it off on extra laptops in the same room to avoid echo), light and dark themes, and a copyable meeting link. The layout adapts to mobile and respects reduced-motion preferences.

## 5. How it works

```
Browser (Angular 21)                       Backend (FastAPI, one process)
 mic → AudioWorklet 16 kHz PCM ──WS──▶  Speech-to-Text v2 streaming (one stream per participant, so speakers are labelled)
 VAD (barge-in)                            │ transcript ─▶ wake-word detector ("Hey Tensor", follow-up window 120 s)
 Web Speech wake hint (UI only)            ▼
 cards / chat / transcript ◀──WS──  ADK supervisor (Gemini) ─▶ knowledge · document · email · jira agents
 Tensor voice (MP3) ◀────────────          │                       │ tools
                                           ▼                       ▼
                               Etiquette router            Vertex AI Search · BigQuery · Document AI output
                     (speak / speak+chat / raise hand / card)        Firestore (meeting state, cards)
                                           │
                               Approval gate (deterministic, never the AI) ─▶ Jira / Gmail executor ─▶ BigQuery audit
```

**One request, end to end.** Vyshnav says *"Hey Tensor, does this spec conflict with our security guidelines?"*
1. Speech-to-Text transcribes the request and the wake detector catches "Hey Tensor". The UI switches to *listening*, then *thinking*.
2. The ADK supervisor sends the request to the Document agent, which reads the parsed PDF and searches the security policy through Vertex AI Search.
3. The answer comes back as a one-line headline plus a detailed markdown table, and numbers are checked against the sources.
4. The etiquette router sees that Sarah is still talking, so Tensor raises its hand and posts the table to the chat.
5. Sarah says *"Go ahead, Tensor"*, and Tensor speaks the short summary.

### Google Cloud services

| Job | Service |
|---|---|
| Understanding, routing, planning | **Gemini on Vertex AI**: `gemini-3.5-flash-lite` for fast steps (about 1 s per call), `gemini-3.8-flash` for document analysis and Jira planning |
| Multi-agent orchestration | **Agent Development Kit (ADK)**: a supervisor agent with four specialist agents as tools |
| Hearing | **Cloud Speech-to-Text v2** streaming, with phrase boost for "Tensor" |
| Speaking | **Cloud Text-to-Speech** (Chirp 3 HD voice) |
| Company knowledge (RAG) | **Vertex AI Search**: one data store whose documents are tagged with `company_id`, and every search is filtered to the user's company. Files are kept in **Cloud Storage** |
| PDFs in chat | **Document AI** Layout Parser, which converts headings, tables and lists to markdown |
| Company data | **BigQuery**: one dataset per company. Model-written SQL is validated by dry run and capped at 100 MB |
| Accounts, companies, live meeting state | **Firestore**: users, sessions, companies, invites, participants, transcript, chat, documents, cards, and the transactional approval gate |
| Audit ledger | **BigQuery** `action_audit` table |
| Hosting | **Cloud Run**: one container for the app, API and WebSockets, with HTTPS and scale to zero |

### Project layout

```
tensor/
├─ start-tensor.cmd        one-click local run (http://localhost:8000)
├─ deploy.cmd              one-click Cloud Run deploy (service account + build + deploy)
├─ Dockerfile              Angular build + FastAPI in one container
├─ PRD.md                  product specification
├─ backend/app/            FastAPI + ADK
│   ├─ main.py             REST + WebSocket API (company-scoped), serves the built frontend
│   ├─ accounts.py         sign-up, sign-in, sessions, companies, invites
│   ├─ provision.py        new-company setup (BigQuery dataset, handbook) and company documents
│   ├─ templates.py        industry templates: team, tables, synthetic data, handbook, example prompts
│   ├─ meeting.py          meeting runtime: wake, follow-ups, voice approvals, hand raise, barge-in
│   ├─ agents.py           ADK supervisor + knowledge / document / email / jira agents, number grounding
│   ├─ etiquette.py        speak vs. chat vs. raise hand vs. card
│   ├─ wake.py             wake word, approval and cancel phrase detection
│   ├─ speech.py           Speech-to-Text streaming, Text-to-Speech
│   ├─ knowledge.py        company-filtered Vertex AI Search + guarded BigQuery SQL
│   ├─ documents.py        Document AI → markdown
│   ├─ actions.py          draft cards, Jira/Gmail executors (live or sandbox), audit log
│   └─ store.py            Firestore (or in-memory) meeting state
├─ backend/tests/          regression tests (no cloud needed)
├─ backend/scripts/demo_e2e.py   full spoken demo through the real speech pipeline
├─ frontend/               Angular 21 meeting room
├─ infra/setup_cloud.py    creates BigQuery tables, Document AI processor, original search engine
├─ infra/setup_tenants.py  creates the multi-company search data store (company_id filter) and engine
├─ infra/seed_demo_company.py  seeds Acme Fintech (data, documents, team) and prints its invite link
├─ infra/delete_company.py removes a company and everything it owns
├─ knowledge/              company knowledge base (8 documents)
└─ demo/                   Q4_Architecture_Spec.pdf used in the demo
```

## 6. What is real and what is simulated

| Real | Sandbox or synthetic |
|---|---|
| Speech-to-Text, Text-to-Speech, Gemini, ADK, Vertex AI Search, Document AI, Firestore, BigQuery | **Jira and Gmail run in sandbox mode.** Approving a card produces sandbox ticket keys (`SEC-401`, …) or a sandbox outbox entry, labelled "Sandbox mode" on screen. Ticket numbers continue across meetings. |
| The approval gate, audit log, etiquette, barge-in, editing by voice | The company (its policies, team and vendor) and the Q4 spec are invented for the demo. The spec has planted conflicts. |
| Accounts, companies, invites, per-company search and data isolation | Company data is **synthetic** and comes from the industry template (120 days ending yesterday, with one planted incident). Acme's `product_metrics` has its incident spike on 2026-10-07 |

**Real Gmail sending** (from your own Gmail account):
1. In the Cloud console, open **Google Auth Platform** and set up the app (External). Add your Gmail as a test user, then **Publish app** so the authorization doesn't expire after 7 days.
2. Create an OAuth client of type **Desktop app** and download its JSON outside the repo (`client_secret*.json` is git-ignored anyway).
3. Run `pip install google-auth-oauthlib`, then `python infra/gmail_auth.py <path-to-client-json>`. Sign in and allow "Send email". The credentials go straight into Secret Manager, and nothing is printed or saved to disk.
4. Run `deploy.cmd`. It sees the stored authorization, gives the service access, and switches Gmail to live.

Real emails only go to **registered members of that company**. Template people and outside addresses are skipped, so Tensor can't email strangers. Replies go to the person who asked Tensor to send.

Live Jira and Gmail executors are built in. To switch them on, set these environment variables and restart; `/api/health` then reports `live`:
- Jira: `TENSOR_JIRA_BASE_URL`, `TENSOR_JIRA_EMAIL`, `TENSOR_JIRA_API_TOKEN`
- Gmail: `TENSOR_GMAIL_CLIENT_ID`, `TENSOR_GMAIL_CLIENT_SECRET`, `TENSOR_GMAIL_REFRESH_TOKEN`, `TENSOR_GMAIL_SENDER`

Keep them in your own environment, never in the repo. In the demo directory, every email address is a plus-address of the operator's own inbox (`demo+david@example.com`), so a real send can never reach a stranger.

## 7. Run it locally

You need Python 3.14, Node 22 or later, and the gcloud CLI.

1. Sign in once for Application Default Credentials. No API keys are used anywhere.
   ```
   gcloud auth application-default login
   gcloud auth application-default set-quota-project ai-builder-nexusforge
   ```
2. Double-click **`start-tensor.cmd`** or run it from a terminal. On the first run it creates `backend\.venv` from `requirements.lock.txt` and builds the Angular app. It then opens **http://localhost:8000**, which serves both the app and the API.
3. Register a company at **/register**, or open the Acme Fintech invite link printed by `infra/seed_demo_company.py`. Then pick a meeting ID and click **Turn on mic**.

To add a second participant, sign in with another account from the same company in a different browser or on another laptop. In a shared physical room, turn off **Tensor voice** on the extra laptops.

### Host it on Cloud Run (so friends can join from anywhere)

Run **`deploy.cmd`**. It does three things:
1. creates the `tensor-run` service account with only the roles Tensor needs;
2. builds the container with Cloud Build;
3. deploys service `tensor` in `asia-south1` and prints the HTTPS URL.

The service settings:
- **Public URL:** the app's own sign-in protects everything behind it.
- **Scales to zero:** after about 15 idle minutes it stops costing anything, and the first visit afterwards takes about 10–15 s to start.
- **At most one instance:** a meeting's live audio state lives in a single process.
- **One-hour request timeout:** long meetings can keep their WebSocket open.

Then run `python infra/seed_demo_company.py --base <URL>` to get an invite link for Acme Fintech on that URL. Sign-ups stop at `TENSOR_MAX_COMPANIES` companies (25 by default) to protect your credits.

**Development mode (hot reload)**

```
cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
cd frontend && npm.cmd start        # http://localhost:4200, proxies /api, /ws, /kb to :8000
```

**Tests**

```
cd backend
set TENSOR_NO_APP=1& set TENSOR_AI=off& .venv\Scripts\python.exe -m pytest -q
```

The tests cover:
- wake-word parsing and etiquette routing;
- punctuation and phonetic wake variants, repeated-name acknowledgement, split speech segments, expiry, and late-audio suppression;
- number grounding;
- the approval gate (an approved card executes exactly once), cancel, and voice edits;
- the raised-hand flow.

They need no cloud access.

`backend/scripts/demo_e2e.py` runs the whole demo through the real speech pipeline. Two simulated participants speak with synthesized voices, the audio streams into Speech-to-Text, and the script prints the timing of each flow. The server must be running.

## 8. Demo script (about 4 minutes)

1. Drag **`demo/Q4_Architecture_Spec.pdf`** into the meeting chat. Document AI parses it in about 5 s ("Parsed · 2 pages, 3 tables").
2. Say: *"Hey Tensor, does this spec conflict with our internal cloud security guidelines?"* Then keep talking about something else.
   - Tensor **raises its hand** ("Found 5 conflicts in the spec.") and posts the full comparison table to the chat, with links to the policy pages.
   - Say *"Go ahead, Tensor"* or click **Let Tensor speak**.
   - The planted conflicts are TLS 1.1 on legacy-settle, 30-day audit logs, and the FastPay vendor (99.5% uptime, Singapore hosting, 24 h breach notice).
3. Say: *"Hey Tensor, turn those conflicts into Jira tickets for the security sprint and assign them to David."*
   - A Jira review card appears. Edit it on screen, or say *"Change the first one to highest priority and go ahead."* The tickets are created and their keys appear on the card.
4. Say: *"Hey Tensor, email David the links to those new tickets and a short summary."*
   - An email card appears that already contains the new ticket keys. Say *"Send it"*.
5. Optional: *"Hey Tensor, what were our daily active users last week?"* or *"…why did payment success drop on October 7th?"*. The numbers come from BigQuery.

Results from the latest full spoken run:

| Step | Result |
|---|---|
| PDF parsed | 4.7 s |
| Wake detected | 3.2 s after the speaker starts |
| Flow 1 | Hand raised at 55 s |
| Flow 2 | Jira card in 24 s; voice edit and approval executed in 20 s |
| Flow 3 | Email draft in 17 s, sent by voice |

## 9. Configuration

All settings are environment variables with working defaults; see [backend/app/config.py](backend/app/config.py).

| Variable | Default | Purpose |
|---|---|---|
| `TENSOR_GCP_PROJECT` | `ai-builder-nexusforge` | Google Cloud project |
| `TENSOR_STORE` | `firestore` | Set `memory` to run without Firestore |
| `TENSOR_AI` | `on` | Set `off` to disable Gemini (tests) |
| `TENSOR_STATIC_DIR` | unset | Built frontend to serve from the backend (set by `start-tensor.cmd`) |
| `TENSOR_FAST_MODEL` / `TENSOR_PLANNER_MODEL` | `gemini-3.5-flash-lite` / `gemini-3.8-flash` | Models |
| `TENSOR_COOKIE_SECURE` | `0` (`1` in the container) | Send the session cookie over HTTPS only |
| `TENSOR_MAX_COMPANIES` | `25` | Cap on registered companies |
| `TENSOR_SEARCH_SERVING_CONFIG` / `TENSOR_SEARCH_BRANCH` | the `tensor-companies` engine / `tensor-companies-kb` data store | Multi-company Vertex AI Search |

`infra/setup_cloud.py` creates the cloud resources: BigQuery tables and seed data, the Document AI processor, and the Vertex AI Search data store and engine. It is safe to re-run and has already been run for this project.

## 10. Known limitations

- **Wake-word feedback takes about 3 s**, because it waits for server-side speech recognition. In Chrome, the browser's own speech recognition shows "listening" sooner. Commands always come from the server transcript.
- **Multi-step answers take 15–55 s** (document comparison, Jira planning). Simple lookups take a few seconds.
- The voice is **Cloud Text-to-Speech**, not Gemini Live native audio. Tensor speaks short, controlled summaries; it is not a free-form voice conversation.
- The ADK agents run **in the backend process**, not on Vertex AI Agent Engine.
- **One server instance.** This handles a few concurrent meetings comfortably. Scaling out would need meeting-to-instance routing or a shared event bus.
- **Accounts are basic.** There is no email verification, password reset or account deletion in the app yet; `infra/delete_company.py` removes a whole company. Each account belongs to one company.
- Company data is synthetic, from industry templates. Connecting a company's real BigQuery tables or uploading CSV files is not built yet.
- **Voice uses a mesh with STUN only (no TURN relay).** This suits about 6 people. On very strict networks (some corporate Wi-Fi, a few mobile carriers), a tile can show *Can't connect audio*; fixing that needs a TURN server. Tensor's own voice is played as an audio file, which the echo canceller doesn't cover, so headphones work best.
- English (en-US) only.
