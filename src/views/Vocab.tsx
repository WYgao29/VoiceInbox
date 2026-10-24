import { useInbox } from "../inbox";
import { createSessionForm, useSessionForm } from "../session-form";

const hotwordsForm = createSessionForm("");
const correctionForm = createSessionForm({ wrong: "", right: "" });

function hotwordLines(text: string): string[] {
  return [...new Set(text.split(/[\n,，、;；]+/).map((item) => item.trim()).filter((item) => item && !item.startsWith("#")))];
}

export function Vocab() {
  const inbox = useInbox();
  const hotwords = useSessionForm(hotwordsForm, inbox.state.hotwordText);
  const correction = useSessionForm(correctionForm);
  const text = hotwords.value;
  const { wrong, right } = correction.value;
  const count = hotwordLines(text).length;
  return (
    <div className="sheet two">
      <form className="panel" aria-busy={hotwords.pending} onSubmit={(event) => {
        event.preventDefault();
        if (inbox.state.busy || hotwords.pending) return;
        void hotwords.submit((draft) => inbox.saveHotwords(draft), {
          success: "常用热词已保存，下一次转写起生效。",
          failure: "常用热词没有保存，请重试。输入已保留。",
          savedValue: (draft) => hotwordLines(draft).join("\n"),
        });
      }}>
        <div className="panel-head">
          <h2 id="hotwords-title">常用热词 {count ? <span className="count">{count}</span> : null}</h2>
          {hotwords.dirty ? <span className="unsaved">有未保存的修改</span> : null}
        </div>
        <p className="help" id="hotwords-description">每次转写都会带上（包括后台定时任务），帮模型认出人名、项目名和术语。一行一个，也可以用逗号或顿号分开。</p>
        <textarea
          aria-labelledby="hotwords-title"
          aria-describedby="hotwords-description"
          rows={8}
          maxLength={8000}
          placeholder="Sol"
          value={text}
          onChange={(event) => hotwords.setValue(event.target.value)}
        />
        <div className="bulk end">
          <button type="submit" className="primary" disabled={inbox.state.busy || hotwords.pending}>
            {hotwords.pending ? "正在保存…" : "保存热词"}
          </button>
        </div>
        <p className="form-status" role="status" aria-live="polite" data-tone={hotwords.feedback?.tone}>{hotwords.feedback?.message}</p>
      </form>
      <section className="panel">
        <div className="panel-head">
          <h2>纠错表 {inbox.state.rules.length ? <span className="count">{inbox.state.rules.length}</span> : null}</h2>
          {correction.dirty ? <span className="unsaved">有未添加的纠错</span> : null}
        </div>
        <p className="help">
          转写后按这张表替换，长的词先换。原始转写不改。删掉一条后，已经替换过的文本要等下次整理，或在命令行跑 <code>vocab apply</code> 才会还原。
        </p>
        <form
          className="fix-row"
          aria-busy={correction.pending}
          onSubmit={(event) => {
            event.preventDefault();
            if (inbox.state.busy || correction.pending) return;
            if (!wrong.trim() || !right.trim()) {
              correction.showError("请填写听错的词和正确的词。");
              return;
            }
            void correction.submit((draft) => inbox.addRule(draft.wrong.trim(), draft.right.trim()), {
              success: "纠错已添加，以后的录音会自动替换。",
              failure: "纠错没有添加，请重试。输入已保留。",
              savedValue: () => ({ wrong: "", right: "" }),
            });
          }}
        >
          <label className="field">
            <span>听成了</span>
            <input value={wrong} maxLength={80} placeholder="扫项目" onChange={(event) => correction.setValue({ ...correction.value, wrong: event.target.value })} />
          </label>
          <label className="field">
            <span>应该是</span>
            <input value={right} maxLength={80} placeholder="Sol 项目" onChange={(event) => correction.setValue({ ...correction.value, right: event.target.value })} />
          </label>
          <button type="submit" className="primary" disabled={inbox.state.busy || correction.pending}>
            {correction.pending ? "正在添加…" : "添加"}
          </button>
        </form>
        <p className="form-status" role="status" aria-live="polite" data-tone={correction.feedback?.tone}>{correction.feedback?.message}</p>
        {inbox.state.rules.length ? (
          <table className="rules">
            <thead>
              <tr>
                <th>听成了</th>
                <th />
                <th>应该是</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {inbox.state.rules.map((rule) => (
                <tr key={`${rule.wrong}\0${rule.right}`}>
                  <td>{rule.wrong}</td>
                  <td className="arrow">→</td>
                  <td>{rule.right}</td>
                  <td>
                    <button type="button" className="text-button danger" onClick={() => void inbox.removeRule(rule.wrong, rule.right)}>
                      删掉
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">还没有纠错规则。</p>
        )}
      </section>
    </div>
  );
}
