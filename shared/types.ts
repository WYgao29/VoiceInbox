export interface Rule {
  wrong: string;
  right: string;
}

export interface MindNode {
  name?: string;
  children?: MindNode[];
}

export interface Todo {
  key: string;
  text: string;
  done?: boolean;
}

export interface Summary {
  overview?: string;
  segments?: { heading?: string; points?: string[] }[];
  decisions?: string[];
  decisions_label?: string;
  extras?: { label?: string; items?: string[] }[];
  mindmap?: { root?: string; children?: MindNode[] };
}

export interface Analysis {
  route?: string;
  route_label?: string;
  type?: string;
  title?: string;
  tags?: string[];
  people?: string[];
  projects?: string[];
  template?: string;
  llm?: boolean;
  model?: string;
  generated_at?: string;
  flash_note?: string;
  summary_md?: string;
  obsidian_path?: string;
  obsidian_label?: string;
  obsidian_uri?: string;
  obsidian_rel?: string;
  state_label?: string;
  error?: string;
  summary?: Summary | null;
  todos?: Todo[];
}

export interface Recording {
  id: string;
  raw_status?: string;
  status_label?: string;
  group?: string;
  source?: string;
  source_label?: string;
  title?: string;
  filename?: string;
  display_title?: string;
  recorded_at?: string;
  recorded_label?: string;
  day_group?: string;
  duration_s?: number | null;
  duration_label?: string;
  attempts?: number;
  reason?: string;
  next_retry_at?: string;
  preview?: string;
  brief?: string;
  scene?: string;
  tags?: string[];
  route?: string;
  summarized?: boolean;
  todo_total?: number;
  todo_open?: number;
  hit_field?: string;
  hit_at?: number | null;
  hit_at_label?: string;
  text?: string;
  raw_text?: string;
  corrected_differs?: boolean;
  has_text?: boolean;
  has_audio?: boolean;
  audio_kind?: string;
  audio_label?: string;
  audio_note?: string;
  analysis?: Analysis | null;
}

export interface Facets {
  kinds?: { flash?: number; full?: number };
  scenes?: { name: string; count: number }[];
  tags?: { name: string; count: number }[];
}

export type ViewName = "today" | "recordings" | "vocab" | "settings";
export type DetailTab = "summary" | "todos" | "mind" | "text";
export type ThemeChoice = "system" | "light" | "dark";

export interface Probe {
  port: number;
  reachable: boolean;
  voiceinbox: boolean;
}

export interface BackendDecision {
  action: "reuse" | "spawn" | "fail";
  port: number;
  owned: boolean;
  reason: string;
}
