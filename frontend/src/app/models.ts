export type TensorStatus = 'idle' | 'listening' | 'thinking' | 'speaking' | 'hand_raised';

export interface TensorState {
  status: TensorStatus;
  awaiting_command?: boolean;
  by?: string;
  command?: string;
  text?: string;
  note?: string;
  hand?: { raised: boolean; topic: string; speech?: string; message_id?: string } | null;
  updated_at?: string;
}

export interface Participant {
  name: string;
  joined_at: string;
  connected: boolean;
}

export interface Segment {
  id: string;
  speaker: string;
  text: string;
  ts: string;
}

export interface ChatMessage {
  id: string;
  author: string;
  kind: 'text' | 'markdown' | 'file' | 'system' | 'export';
  streaming?: boolean;
  text: string;
  ts: string;
  delivery?: string;
  tools?: string[];
  attachment?: { doc_id?: string; card_id?: string; name: string; kind?: string };
}

export interface MeetingDocument {
  id: string;
  name: string;
  status: 'PARSING' | 'READY' | 'FAILED';
  pages?: number;
  tables?: number;
  uploaded_by?: string;
  error?: string;
}

export interface Contact {
  name: string;
  email: string;
}

export interface JiraTask {
  temp_id: number;
  title: string;
  description: string;
  acceptance_criteria: string[];
  story_points: number;
  priority: string;
  assignee: string;
  include: boolean;
}

export type CardStatus =
  'PENDING_REVIEW' | 'MODIFIED' | 'APPROVED' | 'EXECUTING' | 'EXECUTED' | 'FAILED' | 'CANCELLED' | 'READY';

export interface ActionCard {
  card_id: string;
  type: 'EMAIL_DRAFT' | 'JIRA_TASK_PLAN' | 'HAND_RAISE_INSIGHT' | 'DOCUMENT';
  status: CardStatus;
  created_at: string;
  updated_at?: string;
  triggered_by: string;
  executor: string;
  grounding_sources: string[];
  history: { at: string; by: string; change: string }[];
  approved_by?: string;
  approval_channel?: string;
  error?: string;
  result?: {
    executor: string;
    refs: string[];
    links?: string[];
    note?: string;
    message_id?: string;
  };
  payload: {
    // email
    to?: Contact[];
    cc?: Contact[];
    subject?: string;
    body?: string;
    unresolved?: string[];
    // jira
    project_key?: string;
    epic_title?: string;
    proposed_tasks?: JiraTask[];
    // document
    title?: string;
    kind?: 'minutes' | 'document';
    markdown?: string;
  };
}

export interface Snapshot {
  meeting: { id: string; title: string; tensor: TensorState };
  participants: Participant[];
  transcript: Segment[];
  chat: ChatMessage[];
  documents: MeetingDocument[];
  cards: ActionCard[];
}

export interface User {
  uid: string;
  email: string;
  name: string;
  role: 'admin' | 'member';
  company_id: string;
}

export interface Example {
  icon: string;
  label: string;
  text: string;
}

export interface Company {
  id: string;
  name: string;
  industry: string;
  industry_label?: string;
  jira_key: string;
  status: 'PROVISIONING' | 'READY' | 'FAILED';
  error?: string | null;
  examples: Example[];
  created_at?: string;
  sandbox: boolean;
  invite_code?: string;
  voice?: string | null;
}

export interface SessionInfo {
  user: User;
  company: Company;
}

export interface Person {
  name: string;
  role: string;
  email: string;
}

export interface Member {
  uid: string;
  name: string;
  email: string;
  role: 'admin' | 'member';
}

export interface DataColumn {
  name: string;
  type: string;
  description: string;
}

export interface DataTable {
  name: string;
  description: string;
  rows: number;
  columns: DataColumn[];
  first_day?: string;
  last_day?: string;
}

export interface CompanyData {
  dataset: string;
  tables: DataTable[];
  status?: string;
}

export interface KnowledgeDoc {
  id: string;
  name: string;
  title: string;
  mime: string;
  size: number;
  status: 'INDEXING' | 'READY' | 'FAILED';
  uploaded_by: string;
  created_at: string;
  error?: string;
}
