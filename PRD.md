# Product Requirements Document (PRD): Tensor — The Live Agentic Meeting Participant

**Document Version:** 1.0
**Product Name:** Tensor (Wake Word: "Hey Tensor")
**Target Stack:** Google Cloud (Gemini on Vertex AI, Vertex AI Agent Engine & ADK, Vertex AI Search, Document AI, Cloud Run, Firestore, BigQuery)

---

## 1. Problem Statement & Vision

### 1.1 Official Problem Statement

Build an AI-powered solution that transforms how individuals and organizations access knowledge, collaborate, make decisions, and complete everyday work. Solutions should demonstrate meaningful improvements in productivity, efficiency, knowledge accessibility, or workflow execution.

### 1.2 The Core Problem We Solve

Enterprise meetings suffer from an "Execution & Context Gap." Traditional AI meeting bots (like Otter or Fireflies) are passive recorders—they generate a post-meeting summary that turns into a manual to-do list of emails to send, PDFs to cross-check, and Jira tickets to create. Conversely, voice assistants lack multi-party meeting etiquette, either interrupting human speakers with long-winded spoken answers or executing actions without visual verification.

### 1.3 Product Vision

Tensor is a real-time, voice-activated agentic participant that joins live collaboration sessions. It listens passively to build rolling context, wakes up on the hook "Hey Tensor", and orchestrates specialized sub-agents (RAG Knowledge, Chat PDF Parser, Email, and Jira Task Planner).

Crucially, Tensor practices **Smart Meeting Etiquette**:

- Short answers are spoken aloud concisely.
- Long or data-heavy answers trigger a Virtual Hand Raise or a rich message posted to the meeting chat so the call is never derailed.
- External actions (sending emails, creating Jira sprints/tickets) use a Visual Human-in-the-Loop (HITL) Draft Card that waits for a verbal "Go ahead" before executing.

## 2. User Personas & Core Use Cases

| Persona | Pain Point | How They Use Tensor |
|---|---|---|
| Engineering Manager / Tech Lead | Wastes 45 minutes after standups creating Jira tickets, breaking down epics, and emailing stakeholders. | Says: "Hey Tensor, break that auth discussion into three Jira subtasks for Sprint 14 and email David the summary." Reviews the pop-up cards and says "Approve all." |
| Product / Operations Lead | Needs instant answers from company wikis or PDFs dropped into the meeting chat without leaving the call. | Drops a vendor PDF into chat and says: "Hey Tensor, check the SLA table in that PDF against our company policy." |
| Meeting Participant | Tired of AI voices talking for 60 seconds straight over human conversation. | Sees Tensor raise its hand when it finds a complex architectural conflict, clicking "Let Tensor Speak" or reading the summary in the side chat. |

## 3. Functional Requirements & Agent Specifications

### 3.1 Real-Time Audio, Transcription & Wake-Word Hook ("Hey Tensor")

- **FR-1.1 (Passive Context Ingestion):** Tensor continuously transcribes multi-speaker audio in real time via Gemini Live API (`runner.run_live()` in ADK) and stores a rolling 15-minute context window in Firestore.
- **FR-1.2 (Wake-Word Activation):** An observer routine monitors the stream for the phonetic hook "Hey Tensor" (or direct chat @Tensor mentions). Until triggered, Tensor remains muted.
- **FR-1.3 (Barge-In Support):** If Tensor is speaking and a human starts talking, Gemini Live API's native barge-in detection immediately halts audio playback.

### 3.2 Etiquette & Modality Router (Speak vs. Chat vs. Raise Hand)

Before generating output, the Supervisor Agent evaluates the Response Complexity & Urgency Score:

| Modality | Trigger Condition | User Experience |
|---|---|---|
| Direct Voice (< 15 seconds) | Quick factual lookup or action confirmation (e.g., "Draft ready for review on screen"). | Tensor speaks naturally via Gemini Live API audio stream. |
| Chat Message Delivery | Long-form answers, code snippets, tables, or multi-bullet RAG summaries. | Tensor says: "I've posted the full breakdown and source links in the chat," and writes a formatted Markdown card to the chat panel. |
| Virtual Hand Raise | Tensor detects a critical blocker/fact-check proactively OR finishes a long background research task while humans are actively talking. | Tensor triggers a pulsing "Tensor Raised Hand ✋ (Has answer on Q3 SLA)" badge in the UI. Speaking only happens if a user says "Go ahead, Tensor" or clicks the badge. |

### 3.3 Sub-Agent 1: Company Knowledge & RAG Agent

- **FR-3.1 (Enterprise Knowledge Base):** Uses Vertex AI Search to query indexed internal repositories (Confluence, Google Drive, Notion, Architecture Decision Records).
- **FR-3.2 (Data Warehouse Grounding):** Queries BigQuery via an ADK SQL tool when a user asks quantitative company questions (e.g., "Hey Tensor, what were our active user numbers last week?").
- **FR-3.3 (Source Citations):** Every answer includes clickable grounding citations in the meeting chat.

### 3.4 Sub-Agent 2: Live Chat & PDF Intelligence Agent

- **FR-4.1 (Chat Attachment Listener):** Monitors the meeting chat stream for uploaded files (PDFs, contracts, architecture specs, invoices).
- **FR-4.2 (Deep Layout Extraction):** Automatically passes uploaded PDFs to Google Cloud Document AI (Layout Parser) to extract nested tables, clauses, and structured forms, indexing them into the current session's active memory in Firestore.
- **FR-4.3 (Cross-Reference Queries):** Allows users to ask "Hey Tensor, does the PDF just shared in chat match our standard security policy?"—combining Document AI output with Vertex AI Search RAG.

### 3.5 Sub-Agent 3: Email Agent (with Voice HITL Loop)

- **FR-5.1 (Contextual Drafting):** Extracts recipient name, subject, and body context from the recent meeting transcript + company directory.
- **FR-5.2 (Visual Draft Card):** Pushes a live `PENDING_REVIEW` card to the UI via Firestore displaying To, CC, Subject, and Body.
- **FR-5.3 (Voice Modification & Approval):** Supports iterative voice edits ("Make it more formal", "CC Sarah") and executes via Gmail/Workspace API upon hearing "Go ahead" or "Send it."

### 3.6 Sub-Agent 4: Jira & Task Planning Agent (with Review Workflow)

- **FR-6.1 (Task & Sprint Breakdown):** When prompted ("Hey Tensor, plan the tasks for this feature"), analyzes the discussion to generate an Epic, User Stories, Acceptance Criteria, Story Points, and suggested Assignees.
- **FR-6.2 (Interactive Review Board):** Renders a Task Planning Review Card in the meeting UI (stored in Firestore). Users can verbally command changes ("Change story points on task 2 to 5 and assign to Rahul") or toggle checkboxes in the UI.
- **FR-6.3 (Batch Jira Execution):** Only pushes tickets to the Jira REST API after explicit verbal or click approval.

## 4. Technical Architecture (Google Cloud Stack Mapping)

```text
+-------------------------------------------------------------------------+
|                   CLIENT LAYER (Web / Meet Add-on UI)                   |
|  [Audio WebRTC/WS]   [Live Chat & PDF Dropzone]   [HITL Review Cards]   |
+---------+-----------------------+--------------------------+------------+
          | (WSS Audio/Events)    | (HTTPS Upload)           | (Real-time Sync)
          v                       v                          v
+-------------------------------------------------------------------------+
|                    CLOUD RUN (API Gateway & Stream Host)                |
|         Manages WebSocket lifecycle, LiveRequestQueue, & Auth           |
+---------------------------------+---------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------------+
|           VERTEX AI AGENT ENGINE & ADK (Multi-Agent Runtime)            |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  | SUPERVISOR AGENT (Gemini 2.5 Flash Live API via ADK run_live())   |  |
|  | - Wake-word ("Hey Tensor") & Intent Router                        |  |
|  | - Etiquette Controller (Voice vs. Raise Hand vs. Chat Output)     |  |
|  +------+------------------+-------------------+------------------+--+  |
|         |                  |                   |                  |     |
|         v                  v                   v                  v     |
|  +--------------+  +---------------+  +----------------+  +-----------+ |
|  | RAG Agent    |  | PDF/Doc Agent |  | Email Agent    |  | Jira/Plan | |
|  | (Company KB) |  | (Chat Files)  |  | (Workspace)    |  | Agent     | |
|  +------+-------+  +-------+-------+  +--------+-------+  +-----+-----+ |
+---------|------------------|-------------------|----------------|-------+
          |                  |                   |                |
          v                  v                   v                v
+-----------------+  +---------------+  +---------------------------------+
| Vertex AI Search|  | Document AI   |  | Firestore (Session & HITL State)|
| (Enterprise KB) |  | (PDF Layout   |  | - Rolling Transcript Context    |
+-----------------+  |  & Form Parse)|  | - Draft Cards (PENDING/APPROVED)|
                     +---------------+  | - Hand-Raise Queue              |
+-----------------+                     +---------------------------------+
| BigQuery        |
| (Metrics &      |
|  Audit Ledger)  |
+-----------------+
```

### How Each Required Google Cloud Technology Is Used

- **Gemini on Vertex AI (Live API & Pro):** `gemini-2.5-flash` powers low-latency bidirectional voice streaming and wake-word detection; `gemini-2.5-pro` handles complex Jira sprint breakdown and multi-document reasoning.
- **Vertex AI Agent Engine & ADK (`google-adk`):** Hosts the hierarchical multi-agent system (SupervisorAgent delegating to RagAgent, DocAgent, EmailAgent, and JiraPlannerAgent), utilizing LiveRequestQueue and SessionService for stateful tool orchestration.
- **Vertex AI Search:** Powers the enterprise RAG knowledge base with built-in document chunking, hybrid search, and source grounding.
- **Document AI:** Processes PDFs dropped into the live meeting chat, extracting complex tables and layout structures into clean Markdown/JSON for the agent context.
- **Cloud Run:** Autoscales the FastAPI/WebSocket backend and the frontend collaboration UI.
- **Firestore:** Acts as the real-time nervous system—syncing rolling transcripts, chat messages, "Hand Raised" states, and interactive `PENDING_REVIEW` cards for Email and Jira tasks to all meeting participants in milliseconds.
- **BigQuery:** Serves both as a structured enterprise data source for the RAG agent and as an immutable AI Action Audit Log recording every approved email and Jira ticket for enterprise governance.

## 5. Core State Machines & Data Schemas

### 5.1 Firestore Schema (`meetings/{meetingId}/action_cards/{cardId}`)

When Tensor drafts an email or a Jira task plan, it writes a document to Firestore that automatically renders in the frontend UI:

```json
{
  "card_id": "act_98234",
  "type": "JIRA_TASK_PLAN", // or "EMAIL_DRAFT" | "HAND_RAISE_INSIGHT"
  "status": "PENDING_REVIEW", // "PENDING_REVIEW" | "MODIFIED" | "APPROVED" | "EXECUTED" | "CANCELLED"
  "created_at": "2026-10-16T10:14:00Z",
  "triggered_by": "Vyshnav",
  "delivery_mode": "UI_CARD_WITH_SHORT_VOICE_PROMPT",
  "payload": {
    "epic_key": "SEC-104",
    "proposed_tasks": [
      {
        "temp_id": 1,
        "title": "Implement OAuth2 token rotation for API gateway",
        "assignee": "David",
        "story_points": 3,
        "priority": "High"
      },
      {
        "temp_id": 2,
        "title": "Add rate-limiting middleware based on uploaded PDF spec",
        "assignee": "Unassigned",
        "story_points": 5,
        "priority": "Medium"
      }
    ]
  },
  "grounding_sources": ["chat_upload:vendor_security_spec.pdf", "confluence:auth_architecture_v2"]
}
```

### 5.2 The Etiquette Decision Logic (Prompt/Router Rule)

```text
IF response_type IN ['EMAIL_DRAFT', 'JIRA_PLAN']:
    1. Write payload to Firestore with status = 'PENDING_REVIEW'.
    2. Speak < 10 words: "I've put the draft on screen. Let me know if I should go ahead."
ELSE IF estimated_spoken_duration > 15_SECONDS OR contains_table_or_code:
    IF humans_currently_speaking == TRUE:
        1. Set Firestore participant_state.hand_raised = TRUE with topic_preview.
        2. Post detailed answer to Meeting Chat.
    ELSE:
        1. Speak 1-sentence TL;DR aloud.
        2. Post full breakdown with Vertex AI Search citations to Meeting Chat.
```

## 6. End-to-End User Flows (Hackathon Demo Script)

### Flow 1: The Non-Intrusive RAG + PDF Check (Chat & Hand-Raise)

- **User Action:** Drops `Q4_Architecture_Spec.pdf` into the meeting chat and asks: "Hey Tensor, does this spec conflict with our internal cloud security guidelines?" Users immediately keep talking about another topic.
- **System Action:** Document AI parses the PDF; Vertex AI Search retrieves the security guidelines. Seeing that humans are actively speaking and the comparison is 4 paragraphs long, Tensor raises its virtual hand ✋ ("Found 2 compliance conflicts in PDF") and posts the detailed comparison table in the chat without interrupting.

### Flow 2: Task Planning & Jira Review

- **User Action:** "Hey Tensor, turn those two compliance conflicts into Jira tickets for the security sprint and assign them to David."
- **System Action:** JiraPlannerAgent creates a Task Review Card on screen showing the two proposed tickets, story points, and acceptance criteria. Tensor says: "I've drafted two Jira tickets on your screen for review."
- **User Action:** "Change the first one to High priority and go ahead."
- **System Action:** Card updates in real time -> creates tickets -> returns live Jira keys (SEC-401, SEC-402).

### Flow 3: Voice-Confirmed Email

- **User Action:** "Hey Tensor, email David the links to those new tickets and attach the summary."
- **System Action:** EmailAgent pops up the draft card. User says "Send it" -> Email fires and logs to BigQuery.

## 7. Non-Functional Requirements & Success Metrics

- **Wake-to-First-Feedback Latency:** Under 800ms from "Hey Tensor" to visual UI acknowledgment (Listening / Thinking... indicator).
- **Zero Unapproved Mutations:** 100% of external write operations (Gmail send, Jira issue creation) are gated behind `status == "APPROVED"` in Firestore.
- **Interruption Prevention Rate:** 0 unprompted spoken monologues exceeding 15 seconds; all lengthy outputs auto-route to Chat or Hand-Raise.
