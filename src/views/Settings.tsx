import { useEffect, useRef, useState, type FormEvent } from "react";
import { useInbox } from "../inbox";
import { MONITOR_WRITE_WARNING, performMonitorWrite, type MonitorWrite } from "../monitor-confirm";

const LANGUAGES: [string, string][] = [
  ["Chinese", "中文"],
  ["English", "英文"],
  ["", "自动判断"],
];

const PRESETS = [1, 5, 15, 30, 60];

export function Settings() {
  const inbox = useInbox();
  const { state } = inbox;
  const monitor = state.monitor;
  const file = state.settingsFile;
  const features = state.featuresFile;
  const [since, setSince] = useState("");
  const [limit, setLimit] = useState("20");
  const [language, setLanguage] = useState("Chinese");
  const [llm, setLlm] = useState(true);
  const [obsidian, setObsidian] = useState(true);
  const [digestOn, setDigestOn] = useState(true);
  const [flash, setFlash] = useState("20");
  const [custom, setCustom] = useState("15");
  const [note, setNote] = useState("");
  const focus = useRef<string>("");

  useEffect(() => {
    if (!file || focus.current.startsWith("tx")) return;
    setSince(file.since || "");
    setLimit(String(file.max_per_run ?? ""));
    setLanguage(file.language || "");
  }, [file]);

  useEffect(() => {
    if (!features || focus.current.startsWith("ft")) return;
    setLlm(Boolean(features.llm_enabled));
    setObsidian(Boolean(features.obsidian_enabled));
    setDigestOn(Boolean(features.digest_enabled));
    setFlash(String(features.flash_max_s ?? 0));
  }, [features]);

  useEffect(() => {
    if (focus.current === "interval") return;
    const minutes = monitor?.desired?.interval_min;
    if (minutes) setCustom(String(minutes));
  }, [monitor?.desired?.interval_min]);

  const on = monitor?.state !== "off";
  const presets = monitor?.presets?.length ? monitor.presets : PRESETS;
  const current = monitor?.desired?.interval_min;
  const system = state.system;
  const names: Record<number, string> = { 0: "正常", 1: "工具或数据目录有问题", 2: "副本读不到语音备忘录" };
  const doctorCode = system?.doctor?.code;
  const launchd = system?.launchd || {};
  const facts: [string, string][] = [
    ["现在", `${monitor?.state_label || "—"}${monitor?.loaded ? `（每 ${monitor.interval_min} 分钟${monitor.watching ? "，新录音立即处理" : ""}）` : ""}`],
    ["上次检查", monitor?.last_run || "—"],
    ["下次大约", monitor?.loaded ? monitor.next_run || "—" : "暂停中，不会自动检查"],
  ];
  const notes = [
    monitor?.pending || (!monitor?.in_sync && monitor ? "实际状态和保存的设置不一致，点一下开关或间隔会重新应用。" : ""),
    "暂停或改间隔时，如果正好在转写，会等这一轮跑完再生效，不会打断。",
  ].filter(Boolean);

  const saveTx = (event: FormEvent) => {
    event.preventDefault();
    const maxPerRun = Number(limit);
    if (!Number.isInteger(maxPerRun) || maxPerRun < 1) return;
    void inbox.saveSettings({ since: since.trim(), max_per_run: maxPerRun, language });
  };

  const saveFt = (event: FormEvent) => {
    event.preventDefault();
    const seconds = Number(flash);
    if (!Number.isInteger(seconds) || seconds < 0) return;
    void inbox.saveFeatures({
      llm_enabled: llm,
      obsidian_enabled: obsidian,
      digest_enabled: digestOn,
      flash_max_s: seconds,
    });
  };

  const languageChoices = LANGUAGES.some(([code]) => code === language) ? LANGUAGES : [...LANGUAGES, [language, `当前：${language}`] as [string, string]];

  const commitMonitor = (action: MonitorWrite, send: () => Promise<unknown>, detail?: string) => {
    void performMonitorWrite({
      confirm: (message) => window.confirm(message),
      action,
      detail,
      send,
    });
  };

  const pythonPath = state.backend?.python || "";
  return (
    <div className="sheet">
      {pythonPath ? (
        <p className="python-banner">
          解释器 {pythonPath}
          {state.backend?.owned ? "（本应用拉起，退出时会关掉）" : "（本次没有拉起新进程，复用已有后端）"}
          {state.backend?.pythonExecutable === false ? " 这个文件目前不可执行。" : ""}
        </p>
      ) : null}
      <section className="panel" aria-label="后台监控" data-state={monitor?.state || ""}>
        <div className="monitor-head">
          <div>
            <h2>后台监控</h2>
            <p className="help">打开后，这台 Mac 会在后台自动发现语音备忘录里的新录音并转写，不用开着这个窗口。关掉后只能在这里手动导入或检查。</p>
          </div>
          <div className="monitor-switch">
            <label className="switch" title="打开或暂停后台监控" aria-describedby="monitor-launchd-warning">
              <input
                type="checkbox"
                role="switch"
                checked={on}
                disabled={!monitor || state.busy}
                onChange={(event) => {
                  const next = event.target.checked;
                  event.currentTarget.checked = !next;
                  commitMonitor(next ? "enable" : "pause", () => inbox.saveMonitor({ enabled: next }));
                }}
              />
              <span className="track">
                <span className="knob" />
              </span>
              <b>{monitor?.state_label || "—"}</b>
            </label>
            <p id="monitor-launchd-warning" className="monitor-warn">
              {MONITOR_WRITE_WARNING}
            </p>
          </div>
        </div>
        <div className="field">
          <span>多久检查一次</span>
          <div className="seg" role="group" aria-label="检查间隔">
            {presets.map((minutes) => (
              <button
                key={minutes}
                type="button"
                className={current === minutes ? "is-on" : ""}
                disabled={state.busy}
                onClick={() => commitMonitor("interval", () => inbox.saveMonitor({ interval_min: minutes }), String(minutes))}
              >
                {minutes} 分钟
              </button>
            ))}
          </div>
          <div className="custom-row">
            <input
              type="number"
              min={1}
              max={1440}
              step={1}
              aria-label="自定义分钟数"
              value={custom}
              onFocus={() => {
                focus.current = "interval";
              }}
              onBlur={() => {
                focus.current = "";
              }}
              onChange={(event) => setCustom(event.target.value)}
            />
            <span>分钟</span>
            <button
              type="button"
              className="secondary"
              disabled={state.busy}
              onClick={() => {
                const value = Number(custom);
                if (!Number.isInteger(value) || value < 1 || value > 1440) {
                  setNote("间隔要是 1 到 1440 之间的整数分钟");
                  return;
                }
                setNote("");
                commitMonitor("interval", () => inbox.saveMonitor({ interval_min: value }), String(value));
              }}
            >
              用这个间隔
            </button>
          </div>
          <small>最少 1 分钟。间隔越短越及时，但每次检查都会扫一遍语音备忘录目录（没有新录音时不会加载模型）。</small>
          {note ? <p className="help">{note}</p> : null}
        </div>
        <div className="field">
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(monitor?.desired?.watch)}
              disabled={!monitor || state.busy}
              onChange={(event) => {
                const next = event.target.checked;
                event.currentTarget.checked = !next;
                commitMonitor("watch", () => inbox.saveMonitor({ watch: next }), next ? "on" : "off");
              }}
            />
            <span>有新录音时立即处理</span>
          </label>
          <small>文件夹一有变化就马上检查一轮，不等上面的间隔（两次之间至少隔 30 秒）。关掉后只按间隔定时检查。</small>
        </div>
        <dl className="facts">
          {facts.map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <p className="help">{notes.join(" ")}</p>
        <button type="button" className="primary" disabled={!monitor || Boolean(monitor.running) || state.busy} onClick={() => commitMonitor("run", () => inbox.runMonitor())}>
          {monitor?.running ? "正在检查…" : "立即检查一次"}
        </button>
      </section>
      <div className="two">
        <form className="panel" onSubmit={saveTx}>
          <h2>转写</h2>
          <label className="field">
            <span>起始日期</span>
            <input
              value={since}
              inputMode="numeric"
              placeholder="2026-10-01"
              autoComplete="off"
              onFocus={() => {
                focus.current = "tx-since";
              }}
              onBlur={() => {
                focus.current = "";
              }}
              onChange={(event) => setSince(event.target.value)}
            />
            <small>只处理这一天和之后的语音备忘录。留空表示不限。手动导入的音频不受限制。</small>
          </label>
          <label className="field">
            <span>每轮最多几条</span>
            <input
              type="number"
              min={1}
              max={10000}
              step={1}
              value={limit}
              onFocus={() => {
                focus.current = "tx-limit";
              }}
              onBlur={() => {
                focus.current = "";
              }}
              onChange={(event) => setLimit(event.target.value)}
            />
            <small>一轮最多新转写几条，避免一次处理太多。</small>
          </label>
          <label className="field">
            <span>识别语言</span>
            <select
              value={language}
              onFocus={() => {
                focus.current = "tx-lang";
              }}
              onBlur={() => {
                focus.current = "";
              }}
              onChange={(event) => setLanguage(event.target.value)}
            >
              {languageChoices.map(([code, label]) => (
                <option key={code || "auto"} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {state.envOverrides.length ? <p className="help">这次打开时，起始日期或语言被启动参数临时盖过了。保存仍然有效，后台任务会用保存后的值。</p> : null}
          <p className="help">保存后，后台任务下一轮生效。</p>
          <button type="submit" className="primary" disabled={state.busy}>
            保存
          </button>
        </form>
        <form className="panel" onSubmit={saveFt}>
          <h2>整理与导出</h2>
          <label className="check">
            <input
              type="checkbox"
              checked={llm}
              onFocus={() => {
                focus.current = "ft-llm";
              }}
              onBlur={() => {
                focus.current = "";
              }}
              onChange={(event) => setLlm(event.target.checked)}
            />
            <span>转写后用本机模型分类和总结</span>
          </label>
          <p className="help">本机模型 {state.featureMeta.model || ""}，只连本机，不用云服务。和转写共用一把锁，不会同时加载。</p>
          <label className="check">
            <input
              type="checkbox"
              checked={obsidian}
              onChange={(event) => setObsidian(event.target.checked)}
            />
            <span>写入 Obsidian</span>
          </label>
          <p className="help">
            写到 {state.featureMeta.vault || "（没设置库）"}。只改程序维护的那一段，你在笔记里写的其他内容会保留。当日摘要在每天 {state.featureMeta.digest_time || "22:30"} 之后生成。
          </p>
          <label className="check">
            <input type="checkbox" checked={digestOn} onChange={(event) => setDigestOn(event.target.checked)} />
            <span>每天晚上生成当日摘要</span>
          </label>
          <label className="field">
            <span>闪念阈值（秒）</span>
            <input
              type="number"
              min={0}
              max={3600}
              step={1}
              value={flash}
              onFocus={() => {
                focus.current = "ft-flash";
              }}
              onBlur={() => {
                focus.current = "";
              }}
              onChange={(event) => setFlash(event.target.value)}
            />
            <small>不超过这个时长的录音算闪念：只润色成一小段，追加到当天的每日笔记。更长的录音做完整总结。</small>
          </label>
          <button type="submit" className="primary" disabled={state.busy}>
            保存
          </button>
        </form>
      </div>
      <section className="panel" aria-label="系统状态">
        <div className="panel-head">
          <h2>系统状态</h2>
          <button type="button" className="secondary" onClick={() => void inbox.refreshSystem()}>
            刷新
          </button>
        </div>
        {system?.lights?.length ? (
          <div className="lights">
            {system.lights.map((item) => (
              <article key={item.title} className="light" data-level={item.level || "amber"}>
                <span className="dot" />
                <div>
                  <h3>{item.title}</h3>
                  <p>{item.label}</p>
                  <p className="help">{item.detail}</p>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="help">正在检查…</p>
        )}
        <p className="help">这个窗口拉起的后端，权限判断可能和后台任务不同。能不能读到语音备忘录，以后台任务的日志为准。</p>
        <details className="fold">
          <summary>检查详情</summary>
          <p className="meta">
            {doctorCode === undefined ? "" : `退出码 ${doctorCode} · ${names[doctorCode] || "其他"}`}
          </p>
          <pre>{system?.doctor?.text || ""}</pre>
        </details>
        <details className="fold">
          <summary>原始日志</summary>
          <dl className="facts">
            {(
              [
                ["状态", String(launchd.state_label || launchd.state || "—")],
                ["pid", String(launchd.pid ?? "—")],
                ["上次退出", String(launchd.last_exit_code ?? "—")],
                ["已跑次数", String(launchd.runs ?? "—")],
                ["程序", String(launchd.program || "—")],
                ["锁", system?.lock?.detail || "—"],
                ["说明", String(launchd.detail || "")],
              ] as [string, string][]
            )
              .filter(([, value]) => value)
              .map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
          </dl>
          <p className="help">{system?.last_scan?.line || "还没有「本轮结束」的日志。"}</p>
          {(system?.logs || []).map((item) => (
            <div key={item.name} className="log-block">
              <h3>{item.name}</h3>
              <pre>{item.text}</pre>
            </div>
          ))}
        </details>
      </section>
    </div>
  );
}
