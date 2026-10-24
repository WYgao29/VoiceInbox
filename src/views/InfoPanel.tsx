import type { RefObject } from "react";
import { openObsidian } from "../api";
import { RichText } from "../components";
import { InfoDrawerShell } from "../info-drawer";
import { useInbox, type TodayTodo } from "../inbox";
import { recordingTitle, shortWhen } from "../../shared/text.js";

function Fact({ name, value }: { name: string; value: string }) {
  return (
    <div>
      <dt>{name}</dt>
      <dd className={value.includes("/") ? "path-value" : undefined} title={value || undefined}>
        {value || "—"}
      </dd>
    </div>
  );
}

function RecordingInfo() {
  const inbox = useInbox();
  const item = inbox.state.detail;
  if (!item || item.id !== inbox.state.selectedId) return <p className="empty">{inbox.state.detailLoading ? "正在读取录音信息…" : inbox.state.detailError ? "录音信息未能读取，请在正文区域重试。" : "选一条录音后，这里显示场景、标签和状态。"}</p>;
  const analysis = item.analysis;
  return (
    <>
      <h2>这条录音</h2>
      <dl className="facts">
        <Fact name="场景" value={analysis?.route === "flash" ? "闪念" : item.scene || item.status_label || ""} />
        <Fact name="状态" value={item.status_label || ""} />
        <Fact name="标签" value={(analysis?.tags || item.tags || []).map((tag) => `#${tag}`).join(" ")} />
        <Fact name="人物" value={(analysis?.people || []).join("、")} />
        <Fact name="项目" value={(analysis?.projects || []).join("、")} />
        <Fact name="音频" value={[item.audio_label, item.duration_label].filter(Boolean).join(" · ")} />
      </dl>
      {analysis?.obsidian_uri ? (
        <button type="button" className="secondary" onClick={() => openObsidian(analysis.obsidian_uri || "")}>
          在 Obsidian 打开
        </button>
      ) : (
        <p className="help">{analysis?.obsidian_label || "还没有写到 Obsidian。"}</p>
      )}
      <details className="fold">
        <summary>技术信息</summary>
        <dl className="facts">
          <Fact name="尝试" value={item.attempts ? String(item.attempts) : ""} />
          <Fact name="编号" value={item.id} />
        </dl>
      </details>
    </>
  );
}

function TodayInfo() {
  const inbox = useInbox();
  const todos = inbox.state.today?.todos || [];
  const open = todos.filter((todo) => !todo.done);
  const shown = inbox.state.showDoneTodos ? todos : open;
  const groups = new Map<string, { title: string; when: string; items: TodayTodo[] }>();
  for (const todo of shown) {
    const current = groups.get(todo.id) || { title: todo.title || "录音", when: todo.when || "", items: [] };
    current.items.push(todo);
    groups.set(todo.id, current);
  }
  return (
    <>
      <div className="panel-head">
        <h2 id="today-todos-heading" tabIndex={-1}>
          待办 {open.length ? <span className="count">{open.length}</span> : null}
        </h2>
        <label className="check">
          <input type="checkbox" checked={inbox.state.showDoneTodos} onChange={(event) => inbox.setShowDoneTodos(event.target.checked)} />
          <span>显示已完成</span>
        </label>
      </div>
      <p className="help">来自所有录音的总结和闪念。勾选只记在本机，不改 Obsidian 笔记。</p>
      <div data-todo-list>
      {groups.size ? (
        Array.from(groups.entries()).map(([id, group]) => (
          <section key={id} className="todo-group">
            <button type="button" className="text-button" onClick={() => void inbox.openRecording(id)}>
              {group.title}
              {group.when ? <small>{shortWhen(group.when)}</small> : null}
            </button>
            <ul className="todo-list">
              {group.items.map((todo) => {
                const operation = `${id}:${todo.key}`;
                const pending = inbox.state.todoPending?.[operation];
                const error = inbox.state.todoErrors?.[operation];
                return (
                <li key={todo.key} className={(pending ?? todo.done) ? "is-done" : ""} aria-busy={pending !== undefined}>
                  <label>
                    <input
                      type="checkbox"
                      checked={pending ?? Boolean(todo.done)}
                      aria-disabled={pending !== undefined}
                      onClick={(event) => { if (pending !== undefined) event.preventDefault(); }}
                      onChange={(event) => {
                        const trigger = event.currentTarget;
                        const list = trigger.closest("[data-todo-list]");
                        const inputs = list ? [...list.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')] : [];
                        const index = inputs.indexOf(trigger);
                        void inbox.toggleTodo(id, todo.key, event.target.checked).then(() => {
                          window.requestAnimationFrame(() => {
                            if (trigger.isConnected || document.activeElement !== document.body) return;
                            const remaining = list ? [...list.querySelectorAll<HTMLInputElement>('input[type="checkbox"]:not(:disabled)')] : [];
                            (remaining[Math.min(index, remaining.length - 1)] || document.getElementById("today-todos-heading"))?.focus();
                          });
                        });
                      }}
                    />
                    <span>
                      <RichText text={todo.text} query="" rules={inbox.state.rules} />
                    </span>
                    {pending !== undefined ? <small className="todo-saving" role="status">保存中…</small> : null}
                  </label>
                  {error ? <p className="inline-status" data-tone="error" role="alert">{error}</p> : null}
                </li>
              );})}
            </ul>
          </section>
        ))
      ) : (
        <p className="empty">这天没有待办。</p>
      )}
      </div>
    </>
  );
}

function VocabInfo() {
  return (
    <>
      <h2>词表怎么用</h2>
      <p className="help">热词在转写时交给模型，提高人名和术语的识别率。纠错表在转写之后替换文本，不改原始转写。</p>
      <p className="help">在一条录音的「原文」里纠正一个词，会马上更新这条的全文、总结和 Obsidian 笔记，并记进这张表。</p>
    </>
  );
}

function SettingsInfo() {
  const inbox = useInbox();
  const activity = inbox.state.activity;
  const backend = inbox.state.backend;
  return (
    <>
      <h2>此刻</h2>
      <p className={`activity-line ${activity?.phase || "idle"}`}>{activity?.text || "读取中…"}</p>
      <p className="help">转写和总结共用一把锁，同一时间只跑一个。</p>
      <p className="help">
        {inbox.state.system?.disclaimer ||
          "从这里拉起的后端，权限判断可能和 launchd 不同。能不能读到语音备忘录，以 launchd 日志为准。"}
      </p>
      {backend ? (
        <p className="help">
          后端 127.0.0.1:{backend.port}
          {backend.owned ? "，退出应用时会关掉。" : "，启动前就已经在跑，退出应用时不关。"}
        </p>
      ) : null}
    </>
  );
}

export function InfoPanel({
  layout,
  open,
  onClose,
  returnFocus,
}: {
  layout: "dock" | "drawer";
  open: boolean;
  onClose: () => void;
  returnFocus: RefObject<HTMLElement | null>;
}) {
  const inbox = useInbox();
  const title = inbox.state.view === "recordings" && inbox.state.detail?.id === inbox.state.selectedId ? recordingTitle(inbox.state.detail) : "";
  return (
    <InfoDrawerShell
      layout={layout}
      open={open}
      onClose={onClose}
      returnFocus={returnFocus}
      title={inbox.state.view === "recordings" ? title || "信息" : "信息"}
    >
      {inbox.state.view === "today" ? <TodayInfo /> : null}
      {inbox.state.view === "recordings" ? <RecordingInfo /> : null}
      {inbox.state.view === "vocab" ? <VocabInfo /> : null}
      {inbox.state.view === "settings" ? <SettingsInfo /> : null}
    </InfoDrawerShell>
  );
}
