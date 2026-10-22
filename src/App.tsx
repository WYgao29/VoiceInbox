import { useCallback, useEffect, useRef, useState } from "react";
import { interpretKey, visibleDetailTabs } from "../shared/text.js";
import type { ViewName } from "../shared/types.js";
import { ConfirmProvider } from "./confirm-dialog";
import { destinationFromHash } from "./hash-view";
import { useDismissable } from "./dismissable";
import { InboxProvider, useInbox } from "./inbox";
import { applyPlaybackRate } from "./playback";
import { Sidebar } from "./Sidebar";
import { LoadingSkeleton } from "./skeleton";
import { MoreIcon } from "./icons";
import { InfoPanel } from "./views/InfoPanel";
import { Recordings } from "./views/Recordings";
import { Settings } from "./views/Settings";
import { Today } from "./views/Today";
import { Vocab } from "./views/Vocab";

const TITLES: Record<ViewName, string> = {
  today: "今天",
  recordings: "录音",
  vocab: "词表",
  settings: "设置",
};

const ACCEPT = "audio/*,.m4a,.qta,.mp3,.wav,.aac,.mp4,.flac,.ogg,.caf,.opus,.mov";

function isTyping(node: EventTarget | null): boolean {
  const active = node as HTMLElement | null;
  if (!active) return false;
  const tag = active.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || active.isContentEditable;
}

function Shell() {
  const inbox = useInbox();
  const inboxRef = useRef(inbox);
  inboxRef.current = inbox;
  const fileRef = useRef<HTMLInputElement | null>(null);
  const infoButton = useRef<HTMLButtonElement | null>(null);
  const menuRoot = useRef<HTMLDivElement | null>(null);
  const dragDepth = useRef(0);
  const [menu, setMenu] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1099px)").matches);
  const closeMenu = useCallback(() => setMenu(false), []);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  useDismissable(menu, closeMenu, menuRoot);
  const { state } = inbox;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1099px)");
    const onChange = () => setNarrow(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    setMenu(false);
    setPanelOpen(false);
  }, [state.view]);

  useEffect(() => {
    const onImport = () => fileRef.current?.click();
    window.addEventListener("voiceinbox-import", onImport);
    return () => window.removeEventListener("voiceinbox-import", onImport);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const api = inboxRef.current;
      const current = api.state;
      const action = interpretKey(
        event.key,
        { meta: event.metaKey, ctrl: event.ctrlKey, alt: event.altKey },
        {
          typing: isTyping(document.activeElement),
          helpOpen: current.helpOpen,
          view: current.view,
          hasDetail: Boolean(current.detail),
          canPlay: Boolean(current.detail?.has_audio && api.audioRef.current),
          visibleTabs: visibleDetailTabs(current.detail),
        },
      );
      if (!action) return;
      if (action.type === "escape") {
        if (current.helpOpen) api.setHelp(false);
        const active = document.activeElement as HTMLElement | null;
        if (active?.id === "query" && current.query) api.setQuery("");
        active?.blur();
        return;
      }
      event.preventDefault();
      if (action.type === "search") api.focusSearch();
      else if (action.type === "move") api.move(action.delta);
      else if (action.type === "tab") api.setTab(action.name);
      else if (action.type === "toggle-play") {
        const audio = api.audioRef.current;
        if (!audio) return;
        applyPlaybackRate(audio, api.state.rate);
        if (audio.paused) void audio.play().catch(() => undefined);
        else audio.pause();
      } else if (action.type === "today") api.setView("today");
      else if (action.type === "help") api.setHelp(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const next = state.view === "recordings" ? (state.selectedId ? `#${state.selectedId}` : "") : `#${state.view}`;
    if (location.hash !== next) history.replaceState(null, "", next || location.pathname + location.search);
  }, [state.view, state.selectedId]);

  useEffect(() => {
    const onHash = () => {
      const dest = destinationFromHash(location.hash);
      const api = inboxRef.current;
      if (!dest) return;
      if (dest.kind === "view" && api.state.view !== dest.view) api.setView(dest.view);
      else if (dest.kind === "recording" && api.state.selectedId !== dest.id) void api.openRecording(dest.id);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const monitor = state.monitor;
  const monitorText = !monitor ? "监控" : monitor.state === "off" ? "监控已暂停" : `监控 · 每 ${monitor.interval_min} 分钟`;
  const todayStamp = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  };

  return (
    <div
      className={panelOpen ? "app-shell is-panel-open" : "app-shell"}
      onDragEnter={(event) => {
        if (![...event.dataTransfer.types].includes("Files")) return;
        event.preventDefault();
        dragDepth.current += 1;
        inbox.setDrop(true);
      }}
      onDragOver={(event) => {
        if ([...event.dataTransfer.types].includes("Files")) event.preventDefault();
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) inbox.setDrop(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        dragDepth.current = 0;
        inbox.setDrop(false);
        const file = event.dataTransfer.files?.[0];
        if (file) void inbox.upload(file);
      }}
    >
      <Sidebar />
      <div className="main">
        <header className="col-head">
          <h1>{TITLES[state.view]}</h1>
          <label className="search-field" title="按 / 开始搜索">
            <span className="sr">搜索</span>
            <input
              id="query"
              ref={inbox.searchRef}
              type="search"
              placeholder="搜索标题、总结、全文…"
              autoComplete="off"
              value={state.query}
              onChange={(event) => inbox.setQuery(event.target.value)}
            />
            <kbd>/</kbd>
          </label>
          <button type="button" className="pill" title="后台任务状态" onClick={() => inbox.setView("settings")}>
            <span className={`dot ${state.activity?.phase || "idle"}`} />
            <span className="pill-text">{state.activity?.text || "读取中…"}</span>
          </button>
          <button type="button" className="pill" data-state={monitor?.state || ""} title="后台监控" onClick={() => inbox.setView("settings")}>
            <span className="dot" />
            <span className="pill-text">{monitorText}</span>
          </button>
          <button ref={infoButton} type="button" className="secondary panel-toggle" aria-expanded={panelOpen} onClick={() => setPanelOpen((open) => !open)}>
            信息
          </button>
          <button type="button" className="primary" onClick={() => fileRef.current?.click()}>
            导入音频
          </button>
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void inbox.upload(file);
            }}
          />
          <div className="menu" ref={menuRoot}>
            <button type="button" className="secondary icon-button" aria-label="更多" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((open) => !open)}>
              <MoreIcon />
            </button>
            {menu ? (
              <div className="menu-pop" role="menu">
                <button
                  type="button"
                  onClick={() => {
                    setMenu(false);
                    void inbox.retryFailed();
                  }}
                >
                  重试所有失败的
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMenu(false);
                    void inbox.digest(todayStamp());
                  }}
                >
                  生成今天的当日摘要
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMenu(false);
                    inbox.setHelp(true);
                  }}
                >
                  键盘快捷键
                </button>
              </div>
            ) : null}
          </div>
        </header>
        {state.importVisible ? (
          <div className="import-panel">
            <p>{state.importName}</p>
            <div className="bar" aria-hidden="true">
              <div style={{ width: `${state.importRatio}%` }} />
            </div>
            <p>{state.importStatus}</p>
          </div>
        ) : null}
        <div className="main-body">
          {!state.ready ? <LoadingSkeleton /> : null}
          {state.ready && state.view === "recordings" ? <Recordings /> : null}
          {state.ready && state.view === "today" ? <Today /> : null}
          {state.ready && state.view === "vocab" ? <Vocab /> : null}
          {state.ready && state.view === "settings" ? <Settings /> : null}
        </div>
      </div>
      {panelOpen && narrow ? (
        <button type="button" className="panel-scrim" aria-label="关闭信息栏" onClick={closePanel} />
      ) : null}
      <InfoPanel layout={narrow ? "drawer" : "dock"} open={panelOpen} onClose={closePanel} returnFocus={infoButton} />
      {state.notice ? (
        <p className="toast" role="status">
          {state.notice}
        </p>
      ) : null}
      {state.dropOver ? <div className="drop-overlay">松开即可导入</div> : null}
      {state.helpOpen ? (
        <div className="scrim" onClick={() => inbox.setHelp(false)}>
          <div className="dialog" role="dialog" aria-modal="true" aria-label="键盘快捷键" onClick={(event) => event.stopPropagation()}>
            <h2>键盘快捷键</h2>
            <dl className="keys">
              <div>
                <dt>
                  <kbd>/</kbd>
                </dt>
                <dd>搜索</dd>
              </div>
              <div>
                <dt>
                  <kbd>Esc</kbd>
                </dt>
                <dd>清空搜索 / 离开输入框</dd>
              </div>
              <div>
                <dt>
                  <kbd>j</kbd> <kbd>k</kbd>
                </dt>
                <dd>下一条 / 上一条录音</dd>
              </div>
              <div>
                <dt>
                  <kbd>1</kbd>–<kbd>4</kbd>
                </dt>
                <dd>纪要 / 待办 / 导图 / 原文</dd>
              </div>
              <div>
                <dt>
                  <kbd>空格</kbd>
                </dt>
                <dd>播放 / 暂停</dd>
              </div>
              <div>
                <dt>
                  <kbd>t</kbd>
                </dt>
                <dd>打开「今天」</dd>
              </div>
              <div>
                <dt>
                  <kbd>?</kbd>
                </dt>
                <dd>打开这个说明</dd>
              </div>
            </dl>
            <div className="dialog-actions">
              <button type="button" className="primary" onClick={() => inbox.setHelp(false)}>
                知道了
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function App() {
  return (
    <ConfirmProvider>
      <InboxProvider>
        <Shell />
      </InboxProvider>
    </ConfirmProvider>
  );
}
