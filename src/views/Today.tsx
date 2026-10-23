import { openObsidian } from "../api";
import { MarkdownView } from "../components";
import { EmptyState } from "../empty-state";
import { useInbox, type TodayData } from "../inbox";
import { todayEmptyCopy } from "../ui-state";
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
  const digestState = digest?.state || (digest?.md ? "ready" : "none");
  const ready = digestState === "ready";
  const rebuilding = digestState === "pending" || digestState === "failed";
  const md = (ready ? digest?.md || "" : "").replace(/^## 概要\n/, "");
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
      {md || items.length || rebuilding ? (
        <article className="panel">
          <div className="panel-head">
            <h3>摘要</h3>
            <div className="bulk">
              {ready && digest?.uri ? (
                <button type="button" className="secondary" onClick={() => openObsidian(digest.uri || "")}>
                  在 Obsidian 打开
                </button>
              ) : null}
              {(today.digest_ready && items.length) || rebuilding ? (
                <button type="button" className="primary" disabled={inbox.state.busy} onClick={() => void inbox.digest(today.date)}>
                  {rebuilding ? "重试生成" : md ? "重新生成" : "生成摘要"}
                </button>
              ) : null}
            </div>
          </div>
          {md ? (
            <MarkdownView text={md} skip={["待办", "当天的录音"]} />
          ) : rebuilding ? (
            <div className="digest-status" role="status">
              <p>{digestState === "failed" ? "摘要重新生成失败。" : "摘要等待重新生成，完成后会自动更新。"}</p>
              {digest?.error ? <p>{digest.error}</p> : null}
            </div>
          ) : (
            <p className="empty">{`这天还没有当日摘要。每天 ${today.digest_time || ""} 之后会自动生成，也可以现在生成。`}</p>
          )}
          {md ? <p className="foot">更新于 {digest?.updated} · {digest?.path}</p> : null}
        </article>
      ) : null}
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
          <EmptyState copy={todayEmptyCopy()} onAction={() => window.dispatchEvent(new Event("voiceinbox-import"))} />
        )}
      </section>
    </div>
  );
}
