import { openObsidian } from "../api";
import { MarkdownView } from "../components";
import { useInbox, type TodayData } from "../inbox";
import { recordingTitle, timeOnly, weekdayLabel } from "../../shared/text.js";

function dayChoices(today: TodayData): { date: string; count: number }[] {
  const days = (today.days || []).slice();
  if (today.date && !days.some((day) => day.date === today.date)) days.unshift({ date: today.date, count: 0 });
  days.sort((left, right) => (left.date < right.date ? 1 : left.date > right.date ? -1 : 0));
  return days;
}

export function Today() {
  const inbox = useInbox();
  const today = inbox.state.today;
  if (!today) return <p className="empty big">正在读取今天…</p>;
  const days = dayChoices(today);
  const index = days.findIndex((day) => day.date === today.date);
  const digest = today.digest;
  const md = (digest?.md || "").replace(/^## 概要\n/, "");
  const items = (today.items || []).slice().reverse();
  return (
    <div className="today-view">
      <div className="today-head">
        <div>
          <p className="eyebrow">当日摘要</p>
          <h2>{weekdayLabel(today.date, Boolean(today.is_today))}</h2>
        </div>
        <div className="bulk">
          <button type="button" className="secondary" disabled={index < 0 || index >= days.length - 1} onClick={() => {
            const next = days[index + 1];
            if (next) inbox.setDay(next.date);
          }}>
            ‹ 前一天
          </button>
          <select aria-label="选日期" value={today.date} onChange={(event) => inbox.setDay(event.target.value)}>
            {days.map((day) => (
              <option key={day.date} value={day.date}>
                {day.date}（{day.count} 条）
              </option>
            ))}
          </select>
          <button type="button" className="secondary" disabled={index <= 0} onClick={() => {
            const next = days[index - 1];
            if (next) inbox.setDay(next.date);
          }}>
            后一天 ›
          </button>
        </div>
      </div>
      <article className="panel">
        <div className="panel-head">
          <h3>摘要</h3>
          <div className="bulk">
            {digest?.uri ? (
              <button type="button" className="secondary" onClick={() => openObsidian(digest.uri || "")}>
                在 Obsidian 打开
              </button>
            ) : null}
            {today.digest_ready && items.length ? (
              <button type="button" className="primary" disabled={inbox.state.busy} onClick={() => void inbox.digest(today.date)}>
                {digest?.md ? "重新生成" : "生成摘要"}
              </button>
            ) : null}
          </div>
        </div>
        {digest?.md ? (
          <MarkdownView text={md} skip={["待办", "当天的录音"]} />
        ) : (
          <p className="empty">
            {items.length
              ? `这天还没有当日摘要。每天 ${today.digest_time || ""} 之后会自动生成，也可以现在生成。`
              : "这天没有录音。"}
          </p>
        )}
        {digest?.md ? <p className="foot">更新于 {digest.updated} · {digest.path}</p> : null}
      </article>
      <section className="panel">
        <div className="panel-head">
          <h3>这天的录音 · {items.length} 条</h3>
        </div>
        {items.length ? (
          <div className="mini-list">
            {items.map((item) => (
              <button key={item.id} type="button" className="mini" onClick={() => void inbox.openRecording(item.id)}>
                <span className="mini-time">{timeOnly(item.recorded_label || "") || item.recorded_label}</span>
                <span className="scene-pill">{item.route === "flash" ? "闪念" : item.scene || item.status_label || "录音"}</span>
                <span className="mini-copy">
                  <b>{recordingTitle(item)}</b>
                  <small>{[item.duration_label, item.brief].filter(Boolean).join(" · ")}</small>
                </span>
                {item.todo_open ? <em className="todo-chip">☐ {item.todo_open}</em> : null}
              </button>
            ))}
          </div>
        ) : (
          <p className="empty">这天没有录音。</p>
        )}
      </section>
    </div>
  );
}
