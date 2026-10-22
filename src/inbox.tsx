import { createContext, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { DetailTab, Facets, Recording, Rule, ThemeChoice, ViewName } from "../shared/types.js";
import { visibleDetailTabs } from "../shared/text.js";
import { api, desktopBackend, uploadFile } from "./api";
import { destinationFromHash } from "./hash-view";
import { applyPlaybackRate } from "./playback";
import { executeRunMonitor } from "./run-monitor";
import { useConfirm, useDismissConfirm } from "./confirm-dialog";
import { applyTheme, readThemeChoice } from "./theme";
import {
  deleteRuleConfirm,
  digestConfirm,
  importConfirm,
  retranscribeConfirm,
  retryFailedConfirm,
  retryOneConfirm,
  runIfConfirmed,
  summarizeConfirm,
} from "./ui-state";

export interface TodayTodo {
  id: string;
  key: string;
  text: string;
  done?: boolean;
  title?: string;
  date?: string;
  when?: string;
}

export interface TodayData {
  date: string;
  is_today?: boolean;
  digest?: { md?: string; path?: string; uri?: string; updated?: string };
  digest_ready?: boolean;
  digest_time?: string;
  items?: Recording[];
  todos?: TodayTodo[];
  days?: { date: string; count: number }[];
}

export interface MonitorStatus {
  desired?: { enabled?: boolean; interval_min?: number; watch?: boolean };
  state?: string;
  state_label?: string;
  loaded?: boolean;
  running?: boolean;
  interval_min?: number;
  watching?: boolean;
  last_run?: string;
  next_run?: string;
  presets?: number[];
  pending?: string;
  in_sync?: boolean;
  message?: string;
}

export interface SettingsFile {
  since?: string;
  max_per_run?: number;
  language?: string;
}

export interface FeaturesFile {
  llm_enabled?: boolean;
  obsidian_enabled?: boolean;
  digest_enabled?: boolean;
  flash_max_s?: number;
}

export interface FeatureMeta {
  model?: string;
  vault?: string;
  digest_time?: string;
  templates?: string[];
}

export interface SystemStatus {
  disclaimer?: string;
  doctor?: { code?: number; text?: string };
  launchd?: Record<string, string | number | null | undefined>;
  lock?: { detail?: string };
  last_scan?: { line?: string } | null;
  lights?: { level?: string; title?: string; label?: string; detail?: string }[];
  logs?: { name?: string; text?: string }[];
}

export interface InboxState {
  ready: boolean;
  view: ViewName;
  theme: ThemeChoice;
  query: string;
  status: string;
  kind: "" | "flash" | "full";
  scene: string;
  tag: string;
  from: string;
  to: string;
  items: Recording[];
  facets: Facets;
  counts: Record<string, number>;
  selectedId: string;
  detail: Recording | null;
  tab: DetailTab;
  seek: number | null;
  showRaw: boolean;
  rate: number;
  today: TodayData | null;
  day: string;
  showDoneTodos: boolean;
  rules: Rule[];
  hotwordText: string;
  settingsFile: SettingsFile | null;
  envOverrides: string[];
  featuresFile: FeaturesFile | null;
  featureMeta: FeatureMeta;
  monitor: MonitorStatus | null;
  system: SystemStatus | null;
  activity: { phase?: string; text?: string } | null;
  notice: string;
  busy: boolean;
  helpOpen: boolean;
  importName: string;
  importStatus: string;
  importRatio: number;
  importVisible: boolean;
  dropOver: boolean;
  backend: DesktopBackend | null;
}

const initial: InboxState = {
  ready: false,
  view: "recordings",
  theme: "system",
  query: "",
  status: "done",
  kind: "",
  scene: "",
  tag: "",
  from: "",
  to: "",
  items: [],
  facets: {},
  counts: {},
  selectedId: "",
  detail: null,
  tab: "summary",
  seek: null,
  showRaw: false,
  rate: 1,
  today: null,
  day: "",
  showDoneTodos: false,
  rules: [],
  hotwordText: "",
  settingsFile: null,
  envOverrides: [],
  featuresFile: null,
  featureMeta: {},
  monitor: null,
  system: null,
  activity: null,
  notice: "",
  busy: false,
  helpOpen: false,
  importName: "",
  importStatus: "",
  importRatio: 0,
  importVisible: false,
  dropOver: false,
  backend: null,
};

interface InboxApi {
  state: InboxState;
  audioRef: RefObject<HTMLAudioElement | null>;
  setView: (view: ViewName) => void;
  setTheme: (theme: ThemeChoice) => void;
  setQuery: (query: string) => void;
  setStatus: (status: string) => void;
  setKind: (kind: "" | "flash" | "full") => void;
  setScene: (scene: string) => void;
  setTag: (tag: string) => void;
  setFrom: (from: string) => void;
  setTo: (to: string) => void;
  clearDates: () => void;
  clearBrowse: () => void;
  select: (id: string, options?: { keep?: boolean; seek?: number | null }) => Promise<void>;
  setTab: (tab: DetailTab) => void;
  setShowRaw: (showRaw: boolean) => void;
  setRate: (rate: number) => void;
  move: (delta: 1 | -1) => void;
  toggleTodo: (id: string, key: string, done: boolean) => Promise<void>;
  setDay: (day: string) => void;
  shiftDay: (delta: number) => void;
  setShowDoneTodos: (show: boolean) => void;
  saveHotwords: (text: string) => Promise<void>;
  addRule: (wrong: string, right: string, applyTo?: string) => Promise<void>;
  removeRule: (wrong: string, right: string) => Promise<void>;
  saveSettings: (body: { since: string; max_per_run: number; language: string }) => Promise<void>;
  saveFeatures: (body: { llm_enabled: boolean; obsidian_enabled: boolean; digest_enabled: boolean; flash_max_s: number }) => Promise<void>;
  saveMonitor: (change: { enabled?: boolean; interval_min?: number; watch?: boolean }) => Promise<void>;
  runMonitor: () => Promise<void>;
  refreshSystem: () => Promise<void>;
  upload: (file: File) => Promise<void>;
  retryOne: (id: string) => Promise<void>;
  retryFailed: () => Promise<void>;
  retranscribe: (id: string, context: string, hotwords: string) => Promise<void>;
  summarize: (id: string, template: string) => Promise<void>;
  digest: (day: string) => Promise<void>;
  openRecording: (id: string) => Promise<void>;
  setHelp: (open: boolean) => void;
  setDrop: (over: boolean) => void;
  focusSearch: () => void;
  searchRef: RefObject<HTMLInputElement | null>;
}

const InboxContext = createContext<InboxApi | null>(null);

export function useInbox(): InboxApi {
  const value = useContext(InboxContext);
  if (!value) throw new Error("Inbox 尚未就绪");
  return value;
}

function listQuery(state: InboxState): string {
  const params = new URLSearchParams();
  params.set("status", state.status || "done");
  if (state.query) params.set("q", state.query);
  if (state.kind) params.set("kind", state.kind);
  if (state.scene) params.set("scene", state.scene);
  if (state.tag) params.set("tag", state.tag);
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  return `/api/recordings?${params.toString()}`;
}

function orderedIds(items: Recording[]): string[] {
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const key of ["today", "yesterday", "earlier"]) {
    for (const item of items) {
      if ((item.day_group || "earlier") === key && !seen.has(item.id)) {
        seen.add(item.id);
        ids.push(item.id);
      }
    }
  }
  for (const item of items) {
    if (!seen.has(item.id)) ids.push(item.id);
  }
  return ids;
}

function detailDirty(audio: HTMLAudioElement | null): boolean {
  const active = document.activeElement as HTMLElement | null;
  if (active && active.id !== "query") {
    const tag = active.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || active.isContentEditable) return true;
  }
  return Boolean(audio && !audio.paused && !audio.ended);
}

export function InboxProvider({ children }: { children: ReactNode }) {
  const ask = useConfirm();
  const dismissConfirm = useDismissConfirm();
  const [state, setState] = useState<InboxState>(() => ({ ...initial, theme: readThemeChoice() }));
  const stateRef = useRef(state);
  const listGen = useRef(0);
  const noticeTimer = useRef(0);
  const queryTimer = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const initialHash = useRef(location.hash);

  const commit = (recipe: (current: InboxState) => InboxState) => {
    const next = recipe(stateRef.current);
    stateRef.current = next;
    setState(next);
  };

  const notify = (text: string) => {
    commit((current) => ({ ...current, notice: text }));
    window.clearTimeout(noticeTimer.current);
    if (!text) return;
    noticeTimer.current = window.setTimeout(() => {
      if (stateRef.current.notice === text) commit((current) => ({ ...current, notice: "" }));
    }, 4200);
  };

  const loadList = async () => {
    const gen = ++listGen.current;
    const data = await api<{ items?: Recording[]; facets?: Facets; counts?: Record<string, number> }>(listQuery(stateRef.current));
    if (gen !== listGen.current) return data;
    commit((current) => ({
      ...current,
      items: data.items || [],
      facets: data.facets || {},
      counts: data.counts || {},
    }));
    return data;
  };

  const select = async (id: string, options?: { keep?: boolean; seek?: number | null }) => {
    const keep = Boolean(options?.keep && stateRef.current.detail?.id === id);
    commit((current) => ({ ...current, selectedId: id }));
    const data = await api<{ item: Recording }>(`/api/recordings/${id}`);
    if (stateRef.current.selectedId !== id) return;
    commit((current) => {
      const visible = visibleDetailTabs(data.item);
      const preferred = keep ? current.tab : data.item.analysis ? "summary" : "text";
      const tab = (visible.includes(preferred) ? preferred : visible[0] || "text") as DetailTab;
      return {
        ...current,
        detail: data.item,
        tab,
        showRaw: keep ? current.showRaw : false,
        seek: options && "seek" in options ? options.seek ?? null : keep ? current.seek : data.item.hit_at ?? null,
      };
    });
  };

  const loadToday = async (day = stateRef.current.day) => {
    const data = await api<TodayData>(day ? `/api/today?date=${encodeURIComponent(day)}` : "/api/today");
    commit((current) => ({ ...current, today: data, day: data.date || day }));
  };

  const loadRules = async () => {
    const data = await api<{ rules?: Rule[] }>("/api/replacements");
    commit((current) => ({ ...current, rules: data.rules || [] }));
  };

  const loadHotwords = async () => {
    const data = await api<{ text?: string }>("/api/hotwords");
    commit((current) => ({ ...current, hotwordText: data.text || "" }));
  };

  const loadSettings = async () => {
    const data = await api<{ file?: SettingsFile; env_overrides?: string[] }>("/api/settings");
    commit((current) => ({
      ...current,
      settingsFile: data.file || {},
      envOverrides: data.env_overrides || [],
    }));
  };

  const loadFeatures = async () => {
    const data = await api<FeatureMeta & { file?: FeaturesFile }>(`/api/features`);
    commit((current) => ({
      ...current,
      featuresFile: data.file || {},
      featureMeta: {
        model: data.model,
        vault: data.vault,
        digest_time: data.digest_time,
        templates: data.templates,
      },
    }));
  };

  const loadMonitor = async () => {
    const data = await api<MonitorStatus>("/api/monitor");
    commit((current) => ({ ...current, monitor: data }));
  };

  const loadSystem = async () => {
    const data = await api<SystemStatus>("/api/system");
    commit((current) => ({ ...current, system: data }));
  };

  const loadActivity = async () => {
    const data = await api<{ phase?: string; text?: string }>("/api/activity");
    commit((current) => ({ ...current, activity: data }));
  };

  const setView = (view: ViewName) => {
    commit((current) => ({ ...current, view }));
    const fail = (error: unknown) => notify(error instanceof Error ? error.message : "请求失败");
    if (view === "today") void loadToday().catch(fail);
    if (view === "vocab") {
      void loadHotwords().catch(fail);
      void loadRules().catch(fail);
    }
    if (view === "settings") {
      void Promise.all([loadSettings(), loadFeatures(), loadMonitor(), loadSystem()]).catch(fail);
    }
  };

  const runJob = async (work: () => Promise<void>, success: string, running: string) => {
    if (stateRef.current.busy) return;
    commit((current) => ({ ...current, busy: true, notice: running }));
    try {
      await work();
      notify(success);
      await loadList();
      const id = stateRef.current.selectedId;
      if (id) await select(id, { keep: true });
      if (stateRef.current.view === "today") await loadToday();
    } catch (error) {
      notify(error instanceof Error ? error.message : "请求失败");
    } finally {
      commit((current) => ({ ...current, busy: false }));
    }
  };

  const apiValue: InboxApi = {
    state,
    audioRef,
    searchRef,
    setView,
    setTheme: (theme) => {
      applyTheme(theme);
      commit((current) => ({ ...current, theme }));
    },
    setQuery: (query) => {
      commit((current) => ({ ...current, query, view: "recordings" }));
      window.clearTimeout(queryTimer.current);
      queryTimer.current = window.setTimeout(() => {
        void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
      }, 200);
    },
    setStatus: (status) => {
      commit((current) => ({ ...current, status }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    setKind: (kind) => {
      commit((current) => ({ ...current, kind, scene: kind === "flash" ? "" : current.scene }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    setScene: (scene) => {
      commit((current) => ({ ...current, scene: current.scene === scene ? "" : scene }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    setTag: (tag) => {
      commit((current) => ({ ...current, tag: current.tag === tag ? "" : tag, view: "recordings" }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    setFrom: (from) => {
      commit((current) => ({ ...current, from }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    setTo: (to) => {
      commit((current) => ({ ...current, to }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    clearDates: () => {
      commit((current) => ({ ...current, from: "", to: "" }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    clearBrowse: () => {
      commit((current) => ({
        ...current,
        query: "",
        kind: "",
        scene: "",
        tag: "",
        from: "",
        to: "",
        status: "all",
        view: "recordings",
      }));
      void loadList().catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    select,
    setTab: (tab) => commit((current) => ({ ...current, tab })),
    setShowRaw: (showRaw) => commit((current) => ({ ...current, showRaw })),
    setRate: (rate) => {
      commit((current) => ({ ...current, rate }));
      if (audioRef.current) applyPlaybackRate(audioRef.current, rate);
    },
    move: (delta) => {
      const ids = orderedIds(stateRef.current.items);
      if (!ids.length) return;
      const current = ids.indexOf(stateRef.current.selectedId);
      const next = current < 0 ? 0 : Math.max(0, Math.min(ids.length - 1, current + delta));
      const id = ids[next];
      if (id) void select(id).catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    toggleTodo: async (id, key, done) => {
      await api("/api/todos", { method: "POST", json: { id, key, done } });
      commit((current) => ({
        ...current,
        detail:
          current.detail?.id === id && current.detail.analysis
            ? {
                ...current.detail,
                analysis: {
                  ...current.detail.analysis,
                  todos: (current.detail.analysis.todos || []).map((todo) => (todo.key === key ? { ...todo, done } : todo)),
                },
                todo_open: (current.detail.analysis.todos || []).filter((todo) => (todo.key === key ? !done : !todo.done)).length,
              }
            : current.detail,
        today: current.today
          ? {
              ...current.today,
              todos: (current.today.todos || []).map((todo) => (todo.id === id && todo.key === key ? { ...todo, done } : todo)),
            }
          : current.today,
      }));
      void loadList().catch(() => undefined);
    },
    setDay: (day) => {
      commit((current) => ({ ...current, day }));
      void loadToday(day).catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    shiftDay: (delta) => {
      const days = stateRef.current.today?.days || [];
      const index = days.findIndex((day) => day.date === stateRef.current.day);
      const next = days[index - delta];
      if (!next) return;
      commit((current) => ({ ...current, day: next.date }));
      void loadToday(next.date).catch((error: unknown) => notify(error instanceof Error ? error.message : "请求失败"));
    },
    setShowDoneTodos: (show) => commit((current) => ({ ...current, showDoneTodos: show })),
    saveHotwords: async (text) => {
      const payload = await api<{ text?: string }>("/api/hotwords", { method: "POST", json: { text } });
      commit((current) => ({ ...current, hotwordText: payload.text || "" }));
      notify("常用热词已保存，下一次转写起生效");
    },
    addRule: async (wrong, right, applyTo) => {
      const payload = await api<{ message?: string }>("/api/replacements", {
        method: "POST",
        json: applyTo ? { wrong, right, apply_to: applyTo } : { wrong, right },
      });
      await loadRules();
      if (applyTo) {
        await loadList();
        await select(applyTo, { keep: true });
      }
      notify(payload.message || (applyTo ? "已加入纠错，这条已经更新" : "已添加。以后的录音会自动替换。"));
    },
    removeRule: async (wrong, right) => {
      await runIfConfirmed(
        () => ask(deleteRuleConfirm(wrong, right)),
        async () => {
          await api("/api/replacements/remove", { method: "POST", json: { wrong } });
          await loadRules();
          notify("已删掉。已经替换过的文本要等下次整理，或在命令行跑 vocab apply 才会还原。");
        },
      );
    },
    saveSettings: async (body) => {
      const payload = await api<{ file?: SettingsFile; env_overrides?: string[] }>("/api/settings", { method: "POST", json: body });
      commit((current) => ({
        ...current,
        settingsFile: payload.file || current.settingsFile,
        envOverrides: payload.env_overrides || [],
      }));
      notify(payload.env_overrides?.length ? "已保存。这次打开仍被启动参数临时盖过。" : "已保存");
    },
    saveFeatures: async (body) => {
      const payload = await api<FeatureMeta & { file?: FeaturesFile }>("/api/features", { method: "POST", json: body });
      commit((current) => ({
        ...current,
        featuresFile: payload.file || current.featuresFile,
        featureMeta: {
          model: payload.model,
          vault: payload.vault,
          digest_time: payload.digest_time,
          templates: payload.templates,
        },
      }));
      notify("已保存。后台任务下一轮生效。");
    },
    saveMonitor: async (change) => {
      if (stateRef.current.busy) return;
      commit((current) => ({ ...current, busy: true }));
      try {
        const data = await api<MonitorStatus>("/api/monitor", { method: "POST", json: change });
        commit((current) => ({ ...current, monitor: data, busy: false }));
        notify(data.message || "已保存");
      } catch (error) {
        commit((current) => ({ ...current, busy: false }));
        notify(error instanceof Error ? error.message : "没有保存");
        void loadMonitor().catch(() => undefined);
      }
    },
    runMonitor: () =>
      executeRunMonitor({
        busy: stateRef.current.busy,
        request: () => api<MonitorStatus>("/api/monitor/run", { method: "POST", json: {} }),
        onStart: () => commit((current) => ({ ...current, busy: true })),
        onSuccess: (data, message) => {
          commit((current) => ({ ...current, monitor: data, busy: false }));
          notify(message);
        },
        onFailure: (message) => {
          commit((current) => ({ ...current, busy: false }));
          notify(message);
          void loadMonitor().catch(() => undefined);
        },
        scheduleRefresh: () => {
          window.setTimeout(() => {
            void loadActivity().catch(() => undefined);
            void loadMonitor().catch(() => undefined);
          }, 3000);
        },
      }),
    refreshSystem: async () => {
      await loadSystem();
    },
    upload: async (file) => {
      if (stateRef.current.busy) return;
      const gate = await runIfConfirmed(() => ask(importConfirm()), async () => undefined);
      if (gate === "cancelled") return;
      commit((current) => ({
        ...current,
        busy: true,
        view: "recordings",
        importVisible: true,
        importName: file.name,
        importRatio: 0,
        importStatus: "正在上传…",
      }));
      try {
        const payload = await uploadFile(file, (ratio, phase) => {
          commit((current) => ({
            ...current,
            importRatio: ratio,
            importStatus: phase === "transcribe" ? "正在转写…" : `正在上传 ${ratio}%`,
          }));
        });
        const label = String(payload.status_label || "");
        const reason = String(payload.reason || "");
        const text = payload.locked
          ? String(payload.error || "这次没有开始转写")
          : payload.id
            ? [label || "已处理", reason].filter(Boolean).join(" · ")
            : String(payload.error || "导入没有完成");
        notify(text);
        const byLabel: Record<string, string> = {
          已转写: "done",
          太短已跳过: "skipped",
          失败: "failed",
          排队中: "pending",
          转写中: "processing",
        };
        const group = byLabel[label];
        if (group && stateRef.current.status !== "all" && stateRef.current.status !== group) {
          commit((current) => ({ ...current, status: group }));
        }
        await loadList();
        if (typeof payload.id === "string") await select(payload.id);
      } catch (error) {
        notify(error instanceof Error ? error.message : "上传失败");
      } finally {
        commit((current) => ({ ...current, busy: false }));
        window.setTimeout(() => commit((current) => ({ ...current, importVisible: false })), 4000);
      }
    },
    retryOne: async (id) => {
      await runIfConfirmed(() => ask(retryOneConfirm()), () =>
        runJob(async () => {
          await api(`/api/recordings/${id}/retry`, { method: "POST" });
        }, "重试结束", "正在转写。这一轮和后台任务共用一把锁，峰值大约 9.6GB。"),
      );
    },
    retryFailed: async () => {
      await runIfConfirmed(() => ask(retryFailedConfirm()), () =>
        runJob(async () => {
          await api("/api/retry", { method: "POST", json: { scope: "failed-and-dead" } });
        }, "失败项已处理", "正在重试失败的录音…"),
      );
    },
    retranscribe: async (id, context, hotwords) => {
      await runIfConfirmed(() => ask(retranscribeConfirm()), () =>
        runJob(async () => {
          await api(`/api/recordings/${id}/retranscribe`, { method: "POST", json: { context, hotwords } });
        }, "重转结束", "正在转写。这一轮和后台任务共用一把锁，峰值大约 9.6GB。"),
      );
    },
    summarize: async (id, template) => {
      await runIfConfirmed(() => ask(summarizeConfirm()), () =>
        runJob(async () => {
          await api(`/api/recordings/${id}/summarize`, { method: "POST", json: { template } });
        }, "总结已更新", "正在用本机模型整理，长录音要一两分钟…"),
      );
    },
    digest: async (day) => {
      const gate = await runIfConfirmed(() => ask(digestConfirm(day)), async () => undefined);
      if (gate === "cancelled") return;
      await runJob(async () => {
        const payload = await api<{ message?: string }>("/api/digest", { method: "POST", json: { date: day } });
        if (payload.message) throw new Error(payload.message);
      }, "当日摘要已写好", "正在生成当日摘要…");
    },
    openRecording: async (id) => {
      commit((current) => ({ ...current, view: "recordings" }));
      await select(id);
    },
    setHelp: (open) => commit((current) => ({ ...current, helpOpen: open })),
    setDrop: (over) => commit((current) => ({ ...current, dropOver: over })),
    focusSearch: () => {
      commit((current) => ({ ...current, view: "recordings" }));
      window.setTimeout(() => {
        searchRef.current?.focus();
        searchRef.current?.select();
      }, 0);
    },
  };

  useEffect(() => {
    applyTheme(stateRef.current.theme);
    let gone = false;
    let shotsOwn = false;
    void (async () => {
      try {
        const [backend] = await Promise.all([desktopBackend(), loadList(), loadRules(), loadFeatures(), loadActivity(), loadMonitor()]);
        if (gone || shotsOwn) return;
        commit((current) => ({ ...current, ready: true, backend }));
        const dest = destinationFromHash(initialHash.current);
        if (dest?.kind === "view") setView(dest.view);
        else if (dest?.kind === "recording") await select(dest.id);
        else if (!shotsOwn && stateRef.current.items[0]) await select(stateRef.current.items[0].id);
      } catch (error) {
        if (!gone) {
          commit((current) => ({ ...current, ready: true }));
          notify(error instanceof Error ? error.message : "页面加载失败");
        }
      }
    })();
    let monitorTick = 0;
    const activityTimer = window.setInterval(() => {
      if (document.hidden) return;
      void loadActivity().catch(() => undefined);
      monitorTick += 1;
      if (stateRef.current.view === "settings" || monitorTick % 6 === 0) void loadMonitor().catch(() => undefined);
    }, 5000);
    const listTimer = window.setInterval(() => {
      if (document.hidden || stateRef.current.busy || stateRef.current.view !== "recordings" || detailDirty(audioRef.current)) return;
      void loadList()
        .then(() => {
          const id = stateRef.current.selectedId;
          if (id) return select(id, { keep: true });
          return undefined;
        })
        .catch(() => undefined);
    }, 8000);
    window.__voiceinboxShot = async (spec) => {
      shotsOwn = true;
      try {
        dismissConfirm();
        const theme: ThemeChoice = spec.theme === "dark" || spec.theme === "light" ? spec.theme : "system";
        applyTheme(theme);
        const view: ViewName =
          spec.view === "today" || spec.view === "vocab" || spec.view === "settings" ? spec.view : "recordings";
        commit((current) => ({
          ...current,
          ready: true,
          theme,
          helpOpen: false,
          dropOver: false,
          view,
          query: spec.empty ? "zzzz-no-match-shot" : "",
          status: spec.empty ? "all" : "done",
          kind: "",
          scene: "",
          tag: "",
          from: "",
          to: "",
          selectedId: spec.empty ? "" : current.selectedId,
          detail: spec.empty ? null : current.detail,
        }));
        if (view === "recordings") {
          await loadList();
          if (spec.empty) {
            commit((current) => ({
              ...current,
              query: "zzzz-no-match-shot",
              status: "all",
              kind: "",
              scene: "",
              tag: "",
              from: "",
              to: "",
              selectedId: "",
              detail: null,
            }));
          } else {
            const id = stateRef.current.items[0]?.id;
            if (id) await select(id, { keep: false });
            else commit((current) => ({ ...current, selectedId: "", detail: null }));
            const tab = spec.tab;
            if (tab === "summary" || tab === "todos" || tab === "mind" || tab === "text") {
              commit((current) => ({ ...current, tab }));
            }
          }
        }
        if (view === "today") await loadToday();
        if (view === "vocab") {
          await loadHotwords();
          await loadRules();
        }
        if (view === "settings") {
          await Promise.all([
            loadSettings().catch(() => undefined),
            loadFeatures().catch(() => undefined),
            loadMonitor().catch(() => undefined),
            loadSystem().catch(() => undefined),
          ]);
        }
        if (spec.dialog) {
          void ask(deleteRuleConfirm("扫项目", "Sol 项目"));
        }
      } catch (error) {
        notify(error instanceof Error ? error.message : "截图准备失败");
      }
      if (spec.dialog) await new Promise((resolve) => window.setTimeout(resolve, 60));
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))));
      return true;
    };
    return () => {
      gone = true;
      window.clearInterval(activityTimer);
      window.clearInterval(listTimer);
      delete window.__voiceinboxShot;
    };
    // The mount effect closes over the first render's loaders, which read stateRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    document.body.dataset.ready = state.ready ? "1" : "0";
    document.body.dataset.view = state.view;
  }, [state.ready, state.view]);

  return <InboxContext.Provider value={apiValue}>{children}</InboxContext.Provider>;
}
