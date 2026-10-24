import { useCallback, useEffect, useRef, useState } from "react";
import { openObsidian } from "../api";
import { MarkdownView, MindMap, RichText } from "../components";
import { useDismissable } from "../dismissable";
import { EmptyState } from "../empty-state";
import { useInbox } from "../inbox";
import { applyPlaybackRate } from "../playback";
import { recordingsEmptyCopy, recordingsEmptyKind, statusTone } from "../ui-state";
import type { Recording, Rule, Todo } from "../../shared/types.js";
import { DAY_LABEL, DAY_ORDER, STATUS_FILTERS, clock, fileStem, markdownTranscript, recordingTitle, summaryTabLabel, timeOnly, visibleDetailTabs } from "../../shared/text.js";

function TodoList({ items, id, rules, query }: { items: Todo[]; id: string; rules: Rule[]; query: string }) {
  const inbox = useInbox();
  if (!items.length) return <p className="empty">没有待办。</p>;
  return (
    <ul className="todo-list">
      {items.map((todo) => {
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
                void inbox.toggleTodo(id, todo.key, event.target.checked);
              }}
            />
            <span>
              <RichText text={todo.text} query={query} rules={rules} />
            </span>
            {pending !== undefined ? <small className="todo-saving" role="status">保存中…</small> : null}
          </label>
          {error ? <p className="inline-status" data-tone="error" role="alert">{error}</p> : null}
        </li>
      );})}
    </ul>
  );
}

function Card({ item }: { item: Recording }) {
  const inbox = useInbox();
  const active = item.id === inbox.state.selectedId;
  return (
    <div className={inbox.state.selecting ? "rec-row is-selecting" : "rec-row"}>
      {inbox.state.selecting ? (
        <label className="rec-check">
          <input
            type="checkbox"
            aria-label={`选择 ${recordingTitle(item)}`}
            checked={inbox.state.checkedIds.includes(item.id)}
            disabled={inbox.state.busy}
            onChange={() => inbox.toggleRecordingSelection(item.id)}
          />
        </label>
      ) : null}
    <button
      type="button"
      className={active ? "rec-card is-on" : "rec-card"}
      data-id={item.id}
      aria-pressed={active}
      onClick={() => void inbox.select(item.id, { seek: item.hit_at ?? null })}
    >
      <span className="rec-top">
        <span className="scene-pill">{item.route === "flash" ? "闪念" : item.scene || item.status_label || "录音"}</span>
        <span className="rec-time">{timeOnly(item.recorded_label || "") || item.recorded_label}</span>
        <span className="rec-dur">{item.duration_label}</span>
      </span>
      <strong>{recordingTitle(item)}</strong>
      <p>{item.preview || item.brief || "没有预览"}</p>
      <span className="rec-meta">
        {(item.tags || []).slice(0, 3).map((tag) => (
          <em key={tag}>#{tag}</em>
        ))}
        {item.todo_open ? <em className="todo-chip">☐ {item.todo_open}</em> : null}
        {item.hit_field ? <em>{item.hit_field}{item.hit_at_label ? ` · ${item.hit_at_label}` : ""}</em> : null}
      </span>
    </button>
    </div>
  );
}

function PlayGlyph({ paused }: { paused: boolean }) {
  return paused ? (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M5 3.2v9.6l8-4.8-8-4.8Z" fill="currentColor" />
    </svg>
  ) : (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4.4 3.2h2.3v9.6H4.4zm4.9 0h2.3v9.6H9.3z" fill="currentColor" />
    </svg>
  );
}

function Player({ id, durationSeconds = 0 }: { id: string; durationSeconds?: number | null }) {
  const inbox = useInbox();
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(durationSeconds || 0);
  const [canSeek, setCanSeek] = useState(false);
  const pendingSeek = useRef<number | null>(null);
  const showError = (audio: HTMLAudioElement) => {
    setLoading(false);
    setPlaying(false);
    pendingSeek.current = null;
    setError(audio.error?.code === 4 ? "音频格式不受支持，或文件无法读取。" : "音频未能播放，请重新加载后再试。");
  };
  const play = async (audio: HTMLAudioElement) => {
    setError("");
    setLoading(true);
    applyPlaybackRate(audio, inbox.state.rate);
    try {
      await audio.play();
      setLoading(false);
    } catch (cause) {
      setLoading(false);
      if (!(cause instanceof Error) || cause.name !== "AbortError") showError(audio);
    }
  };
  return (
    <div className="player-bar" role="group" aria-label="音频播放器" aria-busy={loading}>
      <audio
        key={id}
        ref={inbox.audioRef}
        preload="metadata"
        tabIndex={-1}
        aria-hidden="true"
        src={`/api/recordings/${id}/audio`}
        onLoadedMetadata={(event) => {
          const audio = event.currentTarget;
          setDuration(Number.isFinite(audio.duration) ? audio.duration : durationSeconds || 0);
          setCanSeek(true);
          applyPlaybackRate(audio, inbox.state.rate);
          if (pendingSeek.current !== null) {
            audio.currentTime = pendingSeek.current;
            pendingSeek.current = null;
            void play(audio);
          }
        }}
        onTimeUpdate={(event) => setTime(event.currentTarget.currentTime || 0)}
        onPlay={(event) => {
          setPlaying(true);
          applyPlaybackRate(event.currentTarget, inbox.state.rate);
        }}
        onPlaying={() => setLoading(false)}
        onWaiting={() => setLoading(true)}
        onCanPlay={() => setLoading(false)}
        onPause={() => { setPlaying(false); setLoading(false); }}
        onEnded={() => { setPlaying(false); setLoading(false); }}
        onError={(event) => { setCanSeek(false); showError(event.currentTarget); }}
      />
      <button
        type="button"
        className="player-toggle"
        aria-label={playing ? "暂停" : loading ? "正在加载音频" : "播放"}
        disabled={loading && !playing}
        onClick={() => {
          const audio = inbox.audioRef.current;
          if (!audio) return;
          if (audio.paused) void play(audio);
          else audio.pause();
        }}
      >
        <PlayGlyph paused={!playing} />
      </button>
      <span className="player-time">{clock(time)}</span>
      <input
        className="player-range"
        type="range"
        min={0}
        max={duration || 0}
        step={0.1}
        value={duration ? Math.min(time, duration) : 0}
        aria-label="播放进度"
        aria-valuetext={`${clock(time)}，共 ${clock(duration)}`}
        disabled={!canSeek}
        onChange={(event) => {
          const next = Number(event.target.value);
          const audio = inbox.audioRef.current;
          if (audio && canSeek) audio.currentTime = next;
          setTime(next);
        }}
      />
      <span className="player-time">{clock(duration)}</span>
      <select aria-label="倍速" value={String(inbox.state.rate)} onChange={(event) => inbox.setRate(Number(event.target.value))}>
        {["0.75", "1", "1.25", "1.5", "2"].map((rate) => (
          <option key={rate} value={rate}>{rate}×</option>
        ))}
      </select>
      {typeof inbox.state.seek === "number" ? (
        <button
          type="button"
          className="secondary"
          disabled={loading}
          onClick={() => {
            const audio = inbox.audioRef.current;
            if (!audio || inbox.state.seek == null) return;
            const target = inbox.state.seek;
            if (audio.readyState >= 1) {
              audio.currentTime = target;
              void play(audio);
            } else {
              pendingSeek.current = target;
              setError("");
              setLoading(true);
              audio.load();
            }
          }}
        >
          跳到命中处 ≈ {clock(inbox.state.seek)}
        </button>
      ) : null}
      {error ? (
        <div className="player-status" data-tone="error" role="alert">
          <span>{error}</span>
          <button type="button" className="text-button" onClick={() => {
            const audio = inbox.audioRef.current;
            if (!audio) return;
            audio.load();
            void play(audio);
          }}>重新加载音频</button>
        </div>
      ) : loading ? <p className="player-status" role="status">正在加载音频…</p> : null}
    </div>
  );
}

export function Recordings() {
  const inbox = useInbox();
  const { state } = inbox;
  const allRef = useRef<HTMLInputElement | null>(null);
  const checkedVisible = state.items.filter((item) => state.checkedIds.includes(item.id)).length;
  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = checkedVisible > 0 && checkedVisible < state.items.length;
  }, [checkedVisible, state.items.length, state.selecting]);
  const emptyKind = recordingsEmptyKind({
    query: state.query,
    kind: state.kind,
    scene: state.scene,
    tag: state.tag,
    from: state.from,
    to: state.to,
    status: state.status,
    total: state.counts.all ?? 0,
  });
  const groups = DAY_ORDER.map((key) => ({
    key,
    label: DAY_LABEL[key],
    items: state.items.filter((item) => (item.day_group || "earlier") === key),
  })).filter((group) => group.items.length);
  return (
    <div className="workspace">
      <section className="list-pane" aria-label="录音列表">
        <div className="recordings-toolbar">
          <h2 id="recordings-list-heading" tabIndex={-1}>录音</h2>
          <button type="button" className="secondary" disabled={state.busy} onClick={() => inbox.setSelecting(!state.selecting)}>
            {state.selecting ? "完成选择" : "选择"}
          </button>
        </div>
        {state.selecting ? (
          <div className="recordings-selection">
            <label>
              <input ref={allRef} type="checkbox" aria-label="全选当前结果" checked={Boolean(state.items.length && checkedVisible === state.items.length)} disabled={state.busy || !state.items.length} onChange={(event) => inbox.selectAllRecordings(event.target.checked)} />
              全选当前结果
            </label>
            <button type="button" className="danger" disabled={state.busy || !state.checkedIds.length} onClick={() => void inbox.deleteRecordings(state.checkedIds)}>
              删除所选（{state.checkedIds.length}）
            </button>
          </div>
        ) : null}
        {state.deleteErrors.length ? (
          <div className="delete-errors" role="status">
            <p>以下录音删除未完成，可重试删除：</p>
            <ul>{state.deleteErrors.map((failure) => <li key={failure.id}>{failure.title}：{failure.error}</li>)}</ul>
            <button type="button" className="secondary" disabled={state.busy} onClick={() => void inbox.deleteRecordings(state.deleteErrors.map((failure) => failure.id))}>重试删除未完成项</button>
          </div>
        ) : null}
        <div className="filters">
          <div className="seg" role="group" aria-label="类型">
            {(
              [
                ["", "全部"],
                ["full", "长录音"],
                ["flash", "闪念"],
              ] as const
            ).map(([kind, label]) => (
              <button key={label} type="button" aria-pressed={state.kind === kind} className={state.kind === kind ? "is-on" : ""} onClick={() => inbox.setKind(kind)}>
                {label}
              </button>
            ))}
          </div>
          <div className="dates">
            <span>时间</span>
            <input id="date-from" type="date" aria-label="从" value={state.from} onChange={(event) => inbox.setFrom(event.target.value)} />
            <span>–</span>
            <input id="date-to" type="date" aria-label="到" value={state.to} onChange={(event) => inbox.setTo(event.target.value)} />
            {state.from || state.to ? (
              <button type="button" className="text-button" onClick={inbox.clearDates}>
                清除
              </button>
            ) : null}
          </div>
          {state.kind !== "flash" && state.facets.scenes?.length ? (
            <div className="chips" aria-label="场景">
              {state.facets.scenes.map((scene) => (
                <button key={scene.name} type="button" aria-pressed={state.scene === scene.name} className={state.scene === scene.name ? "chip is-on" : "chip"} onClick={() => inbox.setScene(scene.name)}>
                  {scene.name}
                  <span>{scene.count}</span>
                </button>
              ))}
            </div>
          ) : null}
          {state.facets.tags?.length ? (
            <div className="chips" aria-label="标签">
              {state.facets.tags.map((tag) => (
                <button key={tag.name} type="button" aria-pressed={state.tag === tag.name} className={state.tag === tag.name ? "chip is-on" : "chip"} onClick={() => inbox.setTag(tag.name)}>
                  #{tag.name}
                  <span>{tag.count}</span>
                </button>
              ))}
            </div>
          ) : null}
          <div className="chips" role="group" aria-label="状态">
            {STATUS_FILTERS.map(([status, label]) => (
              <button key={status} type="button" aria-pressed={state.status === status} className={state.status === status ? "chip is-on" : "chip"} onClick={() => inbox.setStatus(status)}>
                {label}
                <span>{state.counts[status] ?? ""}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="cards">
          {groups.map((group) => (
            <section key={group.key}>
              <h2>{group.label}</h2>
              {group.items.map((item) => (
                <Card key={item.id} item={item} />
              ))}
            </section>
          ))}
          {!state.items.length ? (
            <EmptyState
              copy={recordingsEmptyCopy(emptyKind)}
              onAction={() => {
                if (emptyKind === "search") inbox.setQuery("");
                else if (emptyKind === "filter") inbox.clearBrowse();
                else window.dispatchEvent(new Event("voiceinbox-import"));
              }}
            />
          ) : null}
        </div>
        <p className="keys-hint">
          <kbd>/</kbd> 搜索 · <kbd>j</kbd>
          <kbd>k</kbd> 上下切换 · <kbd>1</kbd>–<kbd>4</kbd> 切标签
        </p>
      </section>
      <Detail />
    </div>
  );
}

function Detail() {
  const inbox = useInbox();
  const item = inbox.state.detail;
  const [context, setContext] = useState("");
  const [hotwords, setHotwords] = useState("");
  const [wrong, setWrong] = useState("");
  const [right, setRight] = useState("");
  const [template, setTemplate] = useState("auto");
  const [toolsMenu, setToolsMenu] = useState<"" | "summary" | "text">("");
  const [copyStatus, setCopyStatus] = useState("");
  const [correctionSaving, setCorrectionSaving] = useState(false);
  const correctionPending = useRef(false);
  const summaryMenu = useRef<HTMLDivElement | null>(null);
  const textMenu = useRef<HTMLDivElement | null>(null);
  const closeTools = useCallback(() => setToolsMenu(""), []);
  useDismissable(toolsMenu === "summary", closeTools, summaryMenu);
  useDismissable(toolsMenu === "text", closeTools, textMenu);
  useEffect(() => {
    setToolsMenu("");
  }, [inbox.state.tab, item?.id]);
  useEffect(() => {
    setCopyStatus("");
  }, [item?.id, item?.text, item?.raw_text, inbox.state.showRaw]);
  if (!item || item.id !== inbox.state.selectedId) {
    return (
      <section className="detail" aria-label="录音详情" aria-busy={inbox.state.detailLoading}>
        {inbox.state.detailError ? (
          <div className="load-state" role="alert">
            <h2>录音未能读取</h2>
            <p>{inbox.state.detailError}</p>
            <button type="button" className="secondary" onClick={() => void inbox.select(inbox.state.selectedId)}>重试读取</button>
          </div>
        ) : <p className="empty big" role="status">{inbox.state.detailLoading ? "正在读取录音…" : "选一条录音，看纪要、待办和原文。"}</p>}
      </section>
    );
  }
  if (item.deletion_pending) {
    return (
      <section className="detail" aria-label="录音详情">
        <div className="detail-head">
          <div className="detail-title-actions">
            <span className="status-pill" data-state="failed">删除未完成</span>
            <button type="button" className="danger" disabled={inbox.state.busy} onClick={() => void inbox.deleteRecordings([item.id])}>重试删除</button>
          </div>
          <h2>{recordingTitle(item)}</h2>
          <p className="meta">{[item.recorded_label, item.duration_label, item.source_label].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="reason-box"><p>{item.reason || "部分内容尚未清理，请重试删除。"}</p></div>
      </section>
    );
  }
  const analysis = item.analysis;
  const tabs = visibleDetailTabs(item);
  const summary = analysis?.summary;
  const shownText = inbox.state.showRaw ? item.raw_text || "" : item.text || "";
  const rules = inbox.state.showRaw ? [] : inbox.state.rules;
  const templates = inbox.state.featureMeta.templates || [];
  const summaryAction = analysis?.route === "flash" ? (analysis.llm ? "重新润色" : "润色") : analysis?.llm ? "重新生成总结" : "生成总结";
  const changeTab = (index: number) => {
    const tab = tabs[(index + tabs.length) % tabs.length];
    if (!tab) return;
    inbox.setTab(tab);
    document.getElementById(`detail-tab-${tab}`)?.focus();
  };
  const copyText = async () => {
    const text = shownText.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus("全文已复制");
    } catch {
      setCopyStatus("未能复制全文，请重试或导出文本。");
    }
  };
  const download = (filename: string, text: string, type: string) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type }));
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  return (
    <section className="detail" aria-label="录音详情">
      <div className="detail-head">
        <div className="detail-title-actions">
        <div className="title-row">
          <span className="scene-pill">{analysis?.route === "flash" ? "闪念" : item.scene || item.status_label}</span>
          {item.group !== "done" ? (
            <span className="status-pill" data-state={statusTone(item.group, item.status_label)}>
              {item.status_label}
            </span>
          ) : null}
          {item.group === "done" && analysis && !analysis.llm ? (
            <span className="status-pill" data-state={statusTone(item.group, analysis.state_label)}>
              {analysis.state_label}
            </span>
          ) : null}
        </div>
        <button type="button" className="text-button danger" disabled={inbox.state.busy} onClick={() => void inbox.deleteRecordings([item.id])}>删除录音</button>
        </div>
        <h2>{recordingTitle(item)}</h2>
        <p className="meta">
          {[item.recorded_label, item.duration_label, item.source_label, item.attempts ? `已尝试 ${item.attempts} 次` : "", item.next_retry_at ? `下次重试 ${item.next_retry_at}` : ""]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {item.has_audio ? <Player key={item.id} id={item.id} durationSeconds={item.duration_s} /> : (
          <p className="help">{item.audio_note || "找不到可以播放的音频。"}</p>
        )}
        {tabs.length ? (
          <div className="action-row tool-row">
            <div className="dtabs" role="tablist" aria-label="录音内容">
              {tabs.map((tab) => (
                <button key={tab} type="button" role="tab" id={`detail-tab-${tab}`} aria-selected={inbox.state.tab === tab} aria-controls={`detail-panel-${tab}`} tabIndex={inbox.state.tab === tab ? 0 : -1} className={inbox.state.tab === tab ? "is-on" : ""} onClick={() => inbox.setTab(tab)} onKeyDown={(event) => {
                  const index = tabs.indexOf(tab);
                  if (event.key === "ArrowRight") { event.preventDefault(); changeTab(index + 1); }
                  else if (event.key === "ArrowLeft") { event.preventDefault(); changeTab(index - 1); }
                  else if (event.key === "Home") { event.preventDefault(); changeTab(0); }
                  else if (event.key === "End") { event.preventDefault(); changeTab(tabs.length - 1); }
                }}>
                  {tab === "summary" ? summaryTabLabel(analysis) : tab === "todos" ? "待办" : tab === "mind" ? "导图" : "原文"}
                  {tab === "todos" ? <span className="count">{(analysis?.todos || []).filter((todo) => !todo.done).length || "✓"}</span> : null}
                </button>
              ))}
            </div>
            <div className="bulk">
              {analysis?.obsidian_uri ? (
                <button type="button" className="secondary" data-collapse onClick={() => openObsidian(analysis.obsidian_uri || "")}>
                  在 Obsidian 打开
                </button>
              ) : null}
              {analysis && analysis.route !== "flash" ? (
                <select aria-label="总结模板" data-collapse value={templates.includes(template) ? template : "auto"} onChange={(event) => setTemplate(event.target.value)}>
                  <option value="auto">模板：自动</option>
                  {templates.map((name) => (
                    <option key={name} value={name}>
                      模板：{name}
                    </option>
                  ))}
                </select>
              ) : null}
              {analysis ? (
                <button type="button" className="secondary" data-collapse disabled={inbox.state.busy} onClick={() => void inbox.summarize(item.id, analysis.route === "flash" ? "auto" : template)}>
                  {summaryAction}
                </button>
              ) : null}
              {analysis ? (
                <div className="menu overflow-menu" ref={summaryMenu}>
                  <button
                    type="button"
                    className="secondary"
                    aria-expanded={toolsMenu === "summary"}
                    onClick={() => setToolsMenu((open) => (open === "summary" ? "" : "summary"))}
                  >
                    更多
                  </button>
                  {toolsMenu === "summary" ? (
                    <div className="menu-pop" role="group" aria-label="录音操作">
                      {analysis.obsidian_uri ? (
                        <button type="button" onClick={() => openObsidian(analysis.obsidian_uri || "")}>
                          在 Obsidian 打开
                        </button>
                      ) : null}
                      {analysis.route !== "flash" ? (
                        <select aria-label="总结模板" value={templates.includes(template) ? template : "auto"} onChange={(event) => setTemplate(event.target.value)}>
                          <option value="auto">模板：自动</option>
                          {templates.map((name) => (
                            <option key={name} value={name}>
                              模板：{name}
                            </option>
                          ))}
                        </select>
                      ) : null}
                      <button type="button" disabled={inbox.state.busy} onClick={() => {
                        setToolsMenu("");
                        void inbox.summarize(item.id, analysis.route === "flash" ? "auto" : template);
                      }}>{summaryAction}</button>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
      {inbox.state.detailError ? <div className="inline-status" data-tone="error" role="alert"><p>录音更新失败：{inbox.state.detailError}</p><button type="button" className="text-button" onClick={() => void inbox.select(item.id, { keep: true })}>重试更新</button></div> : null}
      <div role={tabs.length ? "tabpanel" : undefined} id={`detail-panel-${inbox.state.tab}`} aria-labelledby={tabs.length ? `detail-tab-${inbox.state.tab}` : undefined} tabIndex={tabs.length ? 0 : undefined}>
      {item.reason ? (
        <div className="reason-box">
          <p>{item.reason}</p>
          {item.group === "failed" ? (
            <button type="button" className="primary" disabled={inbox.state.busy} onClick={() => void inbox.retryOne(item.id)}>
              重试这条
            </button>
          ) : null}
        </div>
      ) : null}
      {inbox.state.tab === "summary" && analysis ? (
        <div className="dpane">
          {analysis.route === "flash" && analysis.flash_note ? (
            <div className="flash-note">
              <RichText text={analysis.flash_note} query={inbox.state.query} />
            </div>
          ) : null}
          {analysis.route === "flash" && analysis.todos?.length ? (
            <section className="sum-block">
              <h3>待办</h3>
              <TodoList items={analysis.todos} id={item.id} rules={inbox.state.rules} query={inbox.state.query} />
            </section>
          ) : null}
          {analysis.route !== "flash" && summary?.overview ? (
            <p className="lead">
              <RichText text={summary.overview} query={inbox.state.query} />
            </p>
          ) : null}
          {summary?.decisions?.length ? (
            <section className="sum-block">
              <h3>{summary.decisions_label || "决策"}</h3>
              <ul>
                {summary.decisions.map((line) => (
                  <li key={line}>
                    <RichText text={line} query={inbox.state.query} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {(summary?.extras || []).map((extra) =>
            extra.items?.length ? (
              <section className="sum-block" key={extra.label}>
                <h3>{extra.label}</h3>
                <ul>
                  {extra.items.map((line) => (
                    <li key={line}>
                      <RichText text={line} query={inbox.state.query} />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null,
          )}
          {summary?.segments?.length ? (
            <section className="sum-block">
              <h3>分段要点</h3>
              {summary.segments.map((segment) => (
                <div key={segment.heading} className="segment">
                  <h4>{segment.heading}</h4>
                  <ul>
                    {(segment.points || []).map((point) => (
                      <li key={point}>
                        <RichText text={point} query={inbox.state.query} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          ) : null}
          {!summary && analysis.summary_md ? <MarkdownView text={analysis.summary_md} query={inbox.state.query} skip={["思维导图", "待办"]} /> : null}
          {!analysis.llm ? (
            <p className="empty">
              {analysis.error
                ? `总结没成功：${analysis.error}`
                : analysis.state_label === "仅转写"
                  ? "本机总结没打开，只有转写。可以在设置里打开，或点上面「生成总结」。"
                  : "还没总结，下一轮会自动处理。"}
            </p>
          ) : null}
          <p className="foot">
            {[
              analysis.template && analysis.route !== "flash" ? `模板：${analysis.template}` : "",
              analysis.model ? `由本机 ${analysis.model} 生成${analysis.generated_at ? `于 ${analysis.generated_at}` : ""}` : "",
              analysis.obsidian_rel ? `${analysis.obsidian_label || "笔记"}：${analysis.obsidian_rel}` : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
      ) : null}
      {inbox.state.tab === "todos" ? (
        <div className="dpane">
          <TodoList items={analysis?.todos || []} id={item.id} rules={inbox.state.rules} query={inbox.state.query} />
          <p className="help">勾选只记在本机，不改 Obsidian 笔记。</p>
        </div>
      ) : null}
      {inbox.state.tab === "mind" ? (
        <div className="dpane">
          <MindMap root={summary?.mindmap?.root || recordingTitle(item)} children={summary?.mindmap?.children || []} />
        </div>
      ) : null}
      {inbox.state.tab === "text" ? (
        <div className="dpane">
          <div className="text-head tool-row">
            {item.corrected_differs ? (
              <div className="seg">
                <button type="button" aria-pressed={!inbox.state.showRaw} className={!inbox.state.showRaw ? "is-on" : ""} onClick={() => inbox.setShowRaw(false)}>
                  纠错后
                </button>
                <button type="button" aria-pressed={inbox.state.showRaw} className={inbox.state.showRaw ? "is-on" : ""} onClick={() => inbox.setShowRaw(true)}>
                  原始转写
                </button>
              </div>
            ) : (
              <h3>转写</h3>
            )}
            <div className="bulk">
              <button type="button" className="secondary" onClick={() => void copyText()}>
                复制全文
              </button>
              <button type="button" className="secondary" data-collapse onClick={() => download(`${fileStem(recordingTitle(item))}.txt`, shownText, "text/plain;charset=utf-8")}>
                导出 txt
              </button>
              <button
                type="button"
                className="secondary"
                data-collapse
                onClick={() =>
                  download(
                    `${fileStem(recordingTitle(item))}.md`,
                    markdownTranscript({
                      title: recordingTitle(item),
                      recordedLabel: item.recorded_label || "",
                      durationLabel: item.duration_label || "",
                      text: shownText,
                    }),
                    "text/markdown;charset=utf-8",
                  )
                }
              >
                导出 md
              </button>
              <div className="menu overflow-menu" ref={textMenu}>
                <button
                  type="button"
                  className="secondary"
                  aria-expanded={toolsMenu === "text"}
                  onClick={() => setToolsMenu((open) => (open === "text" ? "" : "text"))}
                >
                  更多
                </button>
                {toolsMenu === "text" ? (
                  <div className="menu-pop" role="group" aria-label="导出转写">
                    <button type="button" onClick={() => download(`${fileStem(recordingTitle(item))}.txt`, shownText, "text/plain;charset=utf-8")}>
                      导出 txt
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        download(
                          `${fileStem(recordingTitle(item))}.md`,
                          markdownTranscript({
                            title: recordingTitle(item),
                            recordedLabel: item.recorded_label || "",
                            durationLabel: item.duration_label || "",
                            text: shownText,
                          }),
                          "text/markdown;charset=utf-8",
                        )
                      }
                    >
                      导出 md
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
          {copyStatus ? <p className="inline-status" role="status">{copyStatus}</p> : null}
          {!inbox.state.showRaw && item.corrected_differs ? <p className="help">带下划线的是按纠错表替换过的词，鼠标停上去能看到原来的写法。</p> : null}
          <div className="reading">{item.has_text ? <RichText text={shownText} query={inbox.state.query} rules={rules} /> : "还没有转写文本。"}</div>
          {item.has_text ? (
            <details className="fold">
              <summary>纠正一个词</summary>
              <p className="help">把常听错的写法换成正确写法。原始转写不动，纠错后的全文、总结和 Obsidian 笔记会马上更新，以后的录音也会自动替换。</p>
              <form
                className="fix-row"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (correctionPending.current || !wrong.trim() || !right.trim()) return;
                  correctionPending.current = true;
                  setCorrectionSaving(true);
                  void inbox.addRule(wrong.trim(), right.trim(), item.id).then((saved) => {
                    if (!saved) return;
                    setWrong("");
                    setRight("");
                  }).finally(() => {
                    correctionPending.current = false;
                    setCorrectionSaving(false);
                  });
                }}
              >
                <label>
                  听成了
                  <input value={wrong} maxLength={80} onChange={(event) => setWrong(event.target.value)} />
                </label>
                <label>
                  应该是
                  <input value={right} maxLength={80} onChange={(event) => setRight(event.target.value)} />
                </label>
                <button type="submit" className="primary" disabled={inbox.state.busy || correctionSaving}>
                  {correctionSaving ? "更新中…" : "加入并更新这条"}
                </button>
              </form>
            </details>
          ) : null}
          {item.group === "done" ? (
            <details className="fold">
              <summary>重新转写</summary>
              <p className="help">可以补充这次录音在谈什么，以及人名、项目名。常用热词每次都会自动带上，不用重复填。</p>
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault();
                  void inbox.retranscribe(item.id, context, hotwords);
                }}
              >
                <label>
                  背景说明
                  <textarea value={context} rows={3} maxLength={4000} onChange={(event) => setContext(event.target.value)} />
                </label>
                <label>
                  这次额外的热词
                  <textarea value={hotwords} rows={2} maxLength={4000} onChange={(event) => setHotwords(event.target.value)} />
                </label>
                <button type="submit" className="primary" disabled={inbox.state.busy}>
                  开始重转
                </button>
              </form>
            </details>
          ) : null}
        </div>
      ) : null}
      </div>
    </section>
  );
}
