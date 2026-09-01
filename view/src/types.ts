// Shapes from docs/log-format.md. Every field a renderer reads is optional
// at the type level because a log written by a newer runtime may carry
// fields this bundle does not know, and a malformed line must never throw.

export interface LogEvent {
  seq: number;
  time: number;
  type: string;
  data: Record<string, unknown>;
}

export type Outcome =
  | { kind: "completed"; value?: unknown }
  | { kind: "blocked"; code?: string; message?: string }
  | { kind: "exhausted"; limit?: string }
  | { kind: "failed"; error?: string }
  | { kind: string };

export interface ForkOrigin {
  episode_id?: string;
  seq?: number;
}

export interface EpisodeStart {
  id?: string;
  parent_id?: string | null;
  fork_origin?: ForkOrigin | null;
  team_id?: string | null;
  contract?: { name?: string; budget?: Budget } & Record<string, unknown>;
  contract_fingerprint?: string;
  task?: string;
  runtime?: { version?: string; build?: string };
  sandbox?: {
    mode?: string;
    landlock_abi?: number;
    resolved_permissions?: {
      read?: unknown[];
      write?: unknown[];
      execute?: unknown[];
      bind_tcp?: number[];
      connect_tcp?: string[];
    };
    process_boundary?: { kind?: string; subtree_cleanup?: string; reason?: string };
  };
}

export interface Budget {
  model_calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  seconds?: number;
}

export interface ToolSchema {
  name?: string;
  description?: string;
  parameters?: unknown;
}

export interface RequestHeader {
  reason?: string;
  system?: string;
  tools?: ToolSchema[];
  model?: { provider?: string; model?: string };
}

export interface ModelRequest {
  step?: number;
  attempt?: number;
  request_id?: string;
  header_seq?: number;
  consumed?: number[];
  messages?: unknown[];
}

export interface ToolCall {
  id?: string;
  name?: string;
  args?: unknown;
}

export interface Usage {
  input?: number;
  output?: number;
  cache_read?: number;
}

export interface AssistantMessage {
  step?: number;
  request_id?: string;
  text?: string;
  tool_calls?: ToolCall[];
  stop?: string;
  usage?: Usage;
  interrupted?: boolean;
}

export interface AssistantChunk {
  step?: number;
  request_id?: string;
  chunk?: {
    kind?: string;
    delta?: string;
    id?: string;
    name?: string;
  };
}

export interface ToolResult {
  step?: number;
  call_id?: string;
  name?: string;
  value?: unknown;
  rendered?: string;
  is_error?: boolean;
  spill?: string | null;
  duration_ms?: number;
  synthetic?: boolean;
}

export interface ContentBlock {
  type?: string;
  text?: string;
  [key: string]: unknown;
}

export interface InboxItem {
  source?: string;
  content?: ContentBlock[];
  from?: string | null;
  message_id?: string | null;
}

export interface SpawnStart {
  child_id?: string;
  contract?: string;
  context?: string;
  call_id?: string;
}

export interface TeamRoster {
  member_id?: string;
  name?: string;
  description?: string;
  phase?: string;
}

/** Reads a string field, returning the fallback for any other value. */
export function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** Reads a finite number field, returning the fallback for any other value. */
export function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Reads an object field, returning an empty object for any other value. */
export function obj(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Reads an array field, returning an empty array for any other value. */
export function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
