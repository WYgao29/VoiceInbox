import { useState, type FormEvent } from "react";
import { useConfirm } from "../confirm-dialog";
import { useInbox } from "../inbox";
import { MONITOR_WRITE_WARNING, performMonitorWrite, type MonitorWrite } from "../monitor-confirm";
import { monitorDialog } from "../ui-state";
import { createSessionForm, useSessionForm } from "../session-form";

const LANGUAGES: [string, string][] = [
  ["Chinese", "中文"],
  ["English", "英文"],
  ["", "自动判断"],
];

const PRESETS = [1, 5, 15, 30, 60];
const transcriptionForm = createSessionForm({ since: "", limit: "20", language: "Chinese" });
const featuresForm = createSessionForm({ llm: true, obsidian: true, digestOn: true, flash: "20" });
const intervalForm = createSessionForm("15");

export function Settings() {
  const inbox = useInbox();
  const ask = useConfirm();
  const { state } = inbox;
  const monitor = state.monitor;
  const file = state.settingsFile;
  const features = state.featuresFile;
  const tx = useSessionForm(transcriptionForm, file ? {
    since: file.since || "",
    limit: String(file.max_per_run ?? ""),
    language: file.language || "",
  } : undefined);
  const ft = useSessionForm(featuresForm, features ? {
    llm: Boolean(features.llm_enabled),
    obsidian: Boolean(features.obsidian_enabled),
    digestOn: Boolean(features.digest_enabled),
    flash: String(features.flash_max_s ?? 0),
  } : undefined);
  const interval = useSessionForm(intervalForm, monitor?.desired?.interval_min ? String(monitor.desired.interval_min) : undefined);
  const { since, limit, language } = tx.value;
  const { llm, obsidian, digestOn, flash } = ft.value;
  const custom = interval.value;
  const [note, setNote] = useState("");

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
    if (tx.pending || state.busy) return;
    const maxPerRun = Number(limit);
    if (!Number.isInteger(maxPerRun) || maxPerRun < 1 || maxPerRun > 10000) {
      tx.showError("每轮条数要是 1 到 10000 之间的整数。");
      return;
    }
    void tx.submit((draft) => inbox.saveSettings({ since: draft.since.trim(), max_per_run: Number(draft.limit), language: draft.language }), {
      success: "转写设置已保存，后台任务下一轮生效。",
      failure: "转写设置没有保存，请重试。输入已保留。",
      savedValue: (draft) => ({ ...draft, since: draft.since.trim(), limit: String(Number(draft.limit)) }),
    });
  };

  const saveFt = (event: FormEvent) => {
    event.preventDefault();
    if (ft.pending || state.busy) return;
    const seconds = Number(flash);
    if (!Number.isInteger(seconds) || seconds < 0 || seconds > 3600) {
      ft.showError("闪念阈值要是 0 到 3600 之间的整数秒。");
      return;
    }
    void ft.submit((draft) => inbox.saveFeatures({
      llm_enabled: draft.llm,
      obsidian_enabled: draft.obsidian,
      digest_enabled: draft.digestOn,
      flash_max_s: Number(draft.flash),
    }), {
      success: "整理与导出设置已保存，后台任务下一轮生效。",
      failure: "整理与导出设置没有保存，请重试。输入已保留。",
      savedValue: (draft) => ({ ...draft, flash: String(Number(draft.flash)) }),
    });
  };

  const languageChoices = LANGUAGES.some(([code]) => code === language) ? LANGUAGES : [...LANGUAGES, [language, `当前：${language}`] as [string, string]];

  const commitMonitor = (action: MonitorWrite, send: () => Promise<unknown>, detail?: string) => {
    void performMonitorWrite({
      confirm: (message) => ask(monitorDialog(message)),
      action,
      detail,
      send,
    });
  };

  const pythonPath = state.backend?.python || "";
  const pythonNote = state.backend?.owned ? "本应用拉起，退出时会关掉" : "本次没有拉起新进程，复用已有后端";
  const pythonBroken = state.backend?.pythonExecutable === false ? "这个文件目前不可执行。" : "";
  return (
    <div className="sheet">
      {pythonPath ? (
        <p className="python-banner" title={`${pythonPath}（${pythonNote}）${pythonBroken}`}>
          <span className="python-banner-label">解释器</span>
          <span className="path-inline">{pythonPath}</span>
          <span className="python-banner-note">
            （{pythonNote}）{pythonBroken}
          </span>
        </p>
      ) : null}
      <section className="panel" aria-label="后台监控" data-state={monitor?.state || ""}>
        <div className="monitor-head">
          <div className="monitor-copy">
            <h2>后台监控</h2>
            <p className="help">打开后，这台 Mac 会在后台自动发现语音备忘录里的新录音并转写，不用开着这个窗口。关掉后只能在这里手动导入或检查。</p>
          </div>
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
        </div>
        <p id="monitor-launchd-warning" className="monitor-warn">
          {MONITOR_WRITE_WARNING}
        </p>
        <div className="field">
          <span>多久检查一次</span>
          <div className="seg" role="group" aria-label="检查间隔">
            {presets.map((minutes) => (
              <button
                key={minutes}
                type="button"
                className={current === minutes ? "is-on" : ""}
                aria-pressed={current === minutes}
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
              onChange={(event) => interval.setValue(event.target.value)}
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
                interval.setValue(String(value));
                commitMonitor("interval", () => inbox.saveMonitor({ interval_min: value }), String(value));
              }}
            >
              用这个间隔
            </button>
          </div>
          <small>最少 1 分钟。间隔越短越及时，但每次检查都会扫一遍语音备忘录目录（没有新录音时不会加载模型）。</small>
          {interval.dirty ? <p className="unsaved">自定义间隔尚未应用</p> : null}
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
        <form className="panel" onSubmit={saveTx} aria-busy={tx.pending}>
          <div className="panel-head"><h2>转写</h2>{tx.dirty ? <span className="unsaved">有未保存的修改</span> : null}</div>
          <label className="field">
            <span>起始日期</span>
            <input
              value={since}
              inputMode="numeric"
              placeholder="2026-10-01"
              autoComplete="off"
              onChange={(event) => tx.setValue({ ...tx.value, since: event.target.value })}
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
              onChange={(event) => tx.setValue({ ...tx.value, limit: event.target.value })}
            />
            <small>一轮最多新转写几条，避免一次处理太多。</small>
          </label>
          <label className="field">
            <span>识别语言</span>
            <select
              value={language}
              onChange={(event) => tx.setValue({ ...tx.value, language: event.target.value })}
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
          <button type="submit" className="primary" disabled={state.busy || tx.pending}>
            {tx.pending ? "正在保存…" : "保存"}
          </button>
          <p className="form-status" role="status" aria-live="polite" data-tone={tx.feedback?.tone}>{tx.feedback?.message}</p>
        </form>
        <form className="panel" onSubmit={saveFt} aria-busy={ft.pending}>
          <div className="panel-head"><h2>整理与导出</h2>{ft.dirty ? <span className="unsaved">有未保存的修改</span> : null}</div>
          <label className="check">
            <input
              type="checkbox"
              checked={llm}
              onChange={(event) => ft.setValue({ ...ft.value, llm: event.target.checked })}
            />
            <span>转写后用本机模型分类和总结</span>
          </label>
          <p className="help">本机模型 {state.featureMeta.model || ""}，只连本机，不用云服务。和转写共用一把锁，不会同时加载。</p>
          <label className="check">
            <input
              type="checkbox"
              checked={obsidian}
              onChange={(event) => ft.setValue({ ...ft.value, obsidian: event.target.checked })}
            />
            <span>写入 Obsidian</span>
          </label>
          <p className="help">写到</p>
          <p className="path-line" title={state.featureMeta.vault || undefined}>
            {state.featureMeta.vault || "（没设置库）"}
          </p>
          <p className="help">只改程序维护的那一段，你在笔记里写的其他内容会保留。当日摘要在每天 {state.featureMeta.digest_time || "22:30"} 之后生成。</p>
          <label className="check">
            <input type="checkbox" checked={digestOn} onChange={(event) => ft.setValue({ ...ft.value, digestOn: event.target.checked })} />
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
              onChange={(event) => ft.setValue({ ...ft.value, flash: event.target.value })}
            />
            <small>不超过这个时长的录音算闪念：只润色成一小段，追加到当天的每日笔记。更长的录音做完整总结。</small>
          </label>
          <button type="submit" className="primary" disabled={state.busy || ft.pending}>
            {ft.pending ? "正在保存…" : "保存"}
          </button>
          <p className="form-status" role="status" aria-live="polite" data-tone={ft.feedback?.tone}>{ft.feedback?.message}</p>
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
                  <dd className={value.includes("/") ? "path-value" : undefined} title={value}>
                    {value}
                  </dd>
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
