import { EmptyIcon } from "./icons";
import type { EmptyCopy } from "./ui-state";

export function EmptyState({ copy, onAction }: { copy: EmptyCopy; onAction: () => void }) {
  return (
    <div className="empty-state">
      <EmptyIcon name={copy.icon} />
      <h2>{copy.title}</h2>
      <p>{copy.hint}</p>
      <button type="button" className="primary" onClick={onAction}>
        {copy.action}
      </button>
    </div>
  );
}
