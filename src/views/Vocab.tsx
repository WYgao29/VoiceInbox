import { useEffect, useRef, useState } from "react";
import { useInbox } from "../inbox";

function wordCount(text: string): number {
  return text.split(/[\n,，、;；]+/).map((item) => item.trim()).filter(Boolean).length;
}

export function Vocab() {
  const inbox = useInbox();
  const [text, setText] = useState(inbox.state.hotwordText);
  const [wrong, setWrong] = useState("");
  const [right, setRight] = useState("");
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(inbox.state.hotwordText);
  }, [inbox.state.hotwordText]);
  const count = wordCount(text);
  return (
    <div className="sheet two">
      <section className="panel">
        <div className="panel-head">
          <h2>常用热词 {count ? <span className="count">{count}</span> : null}</h2>
        </div>
        <p className="help">每次转写都会带上（包括后台定时任务），帮模型认出人名、项目名和术语。一行一个，也可以用逗号或顿号分开。</p>
        <textarea
          rows={8}
          maxLength={8000}
          placeholder="Sol"
          value={text}
          onFocus={() => {
            focused.current = true;
          }}
          onBlur={() => {
            focused.current = false;
          }}
          onChange={(event) => setText(event.target.value)}
        />
        <div className="bulk end">
          <button type="button" className="primary" disabled={inbox.state.busy} onClick={() => void inbox.saveHotwords(text)}>
            保存热词
          </button>
        </div>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>纠错表 {inbox.state.rules.length ? <span className="count">{inbox.state.rules.length}</span> : null}</h2>
        </div>
        <p className="help">
          转写后按这张表替换，长的词先换。原始转写不改。删掉一条后，已经替换过的文本要等下次整理，或在命令行跑 <code>vocab apply</code> 才会还原。
        </p>
        <form
          className="fix-row"
          onSubmit={(event) => {
            event.preventDefault();
            const left = wrong.trim();
            const next = right.trim();
            if (!left || !next || inbox.state.busy) return;
            void inbox.addRule(left, next).then(() => {
              setWrong("");
              setRight("");
            });
          }}
        >
          <label className="field">
            <span>听成了</span>
            <input value={wrong} maxLength={80} placeholder="扫项目" onChange={(event) => setWrong(event.target.value)} />
          </label>
          <label className="field">
            <span>应该是</span>
            <input value={right} maxLength={80} placeholder="Sol 项目" onChange={(event) => setRight(event.target.value)} />
          </label>
          <button type="submit" className="primary" disabled={inbox.state.busy}>
            添加
          </button>
        </form>
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
