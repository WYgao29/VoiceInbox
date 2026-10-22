import type { ReactNode } from "react";
import type { MindNode, Rule } from "../shared/types.js";
import { decorateText, parseMarkdown, type MdBlock } from "../shared/text.js";

export function RichText({ text, query = "", rules = [] }: { text: string; query?: string; rules?: Rule[] }) {
  const pieces = decorateText(text || "", query, rules);
  const nodes: ReactNode[] = [];
  let index = 0;
  while (index < pieces.length) {
    const piece = pieces[index];
    if (!piece.title) {
      nodes.push(piece.mark ? <mark key={index}>{piece.text}</mark> : <span key={index}>{piece.text}</span>);
      index += 1;
      continue;
    }
    const title = piece.title;
    const group = [];
    while (index < pieces.length && pieces[index].title === title) {
      const current = pieces[index];
      group.push(current.mark ? <mark key={`${index}-m`}>{current.text}</mark> : current.text);
      index += 1;
    }
    nodes.push(
      <span key={title + index} className="fixed" title={title}>
        {group}
      </span>,
    );
  }
  return <>{nodes}</>;
}

function Block({ block, query }: { block: MdBlock; query: string }) {
  if (block.type === "heading") {
    const Tag = block.level <= 2 ? "h2" : block.level === 3 ? "h3" : "h4";
    return (
      <Tag className="md-h">
        <RichText text={block.text} query={query} />
      </Tag>
    );
  }
  if (block.type === "code") {
    return (
      <pre className="md-code">
        <code>{block.text}</code>
      </pre>
    );
  }
  if (block.type === "list") {
    return (
      <ul className={block.todo ? "md-todo" : "md-list"}>
        {block.items.map((item, index) => (
          <li key={index}>
            {item.checked === null ? null : <input type="checkbox" disabled checked={item.checked} readOnly />}
            <RichText text={item.text} query={query} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p className={block.note ? "md-note" : "md-p"}>
      <RichText text={block.text} query={query} />
    </p>
  );
}

export function MarkdownView({ text, query = "", skip = [] }: { text: string; query?: string; skip?: string[] }) {
  const blocks = parseMarkdown(text, skip);
  if (!blocks.length) return null;
  return (
    <div className="markdown">
      {blocks.map((block, index) => (
        <Block key={index} block={block} query={query} />
      ))}
    </div>
  );
}

function MindNodeView({ node, depth }: { node: MindNode; depth: number }) {
  return (
    <li>
      <span className={`node d${Math.min(depth, 3)}`}>{node.name || ""}</span>
      {node.children?.length ? (
        <ul>
          {node.children.map((child, index) => (
            <MindNodeView key={index} node={child} depth={depth + 1} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function MindMap({ root, children }: { root: string; children: MindNode[] }) {
  if (!children.length) return <p className="empty">没有导图。</p>;
  return (
    <div className="mindmap">
      <ul className="tree">
        <MindNodeView node={{ name: root, children }} depth={0} />
      </ul>
    </div>
  );
}
