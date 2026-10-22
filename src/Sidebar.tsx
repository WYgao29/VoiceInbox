import { APP_VERSION } from "../shared/version.js";
import type { ThemeChoice, ViewName } from "../shared/types.js";
import { useInbox } from "./inbox";

const NAV: { view: ViewName; label: string }[] = [
  { view: "today", label: "今天" },
  { view: "recordings", label: "录音" },
  { view: "vocab", label: "词表" },
  { view: "settings", label: "设置" },
];

export function Sidebar() {
  const inbox = useInbox();
  const { state } = inbox;
  const backend = state.backend
    ? `后端 ${state.backend.port} · ${state.backend.owned ? "随应用退出" : "已在运行"}`
    : "后端连接中";
  return (
    <aside className="sidebar">
      <div className="brand-row">
        <img className="brand-mark is-light" src="/brand/voiceinbox-mark.svg" width="22" height="22" alt="" />
        <img className="brand-mark is-dark" src="/brand/voiceinbox-mark-dark.svg" width="22" height="22" alt="" />
        <span className="wordmark">VoiceInbox</span>
      </div>
      <nav className="nav" aria-label="页面">
        {NAV.map((item) => (
          <button
            key={item.view}
            type="button"
            className={state.view === item.view ? "is-on" : ""}
            onClick={() => inbox.setView(item.view)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <footer className="sidebar-foot">
        <label className="theme-field">
          <span>外观</span>
          <select
            aria-label="外观"
            value={state.theme}
            onChange={(event) => inbox.setTheme(event.target.value as ThemeChoice)}
          >
            <option value="system">跟随系统</option>
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
        </label>
        <p className="version">v{APP_VERSION} · 仅本机</p>
        <p className="version-sub">{backend}</p>
      </footer>
    </aside>
  );
}
