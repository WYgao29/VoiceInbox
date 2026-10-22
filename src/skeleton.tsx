export function LoadingSkeleton() {
  return (
    <div className="skeleton-screen" role="status" aria-label="正在读取">
      <div className="skeleton-col">
        <div className="skeleton-line w-40" />
        <div className="skeleton-line w-70" />
        <div className="skeleton-card" />
        <div className="skeleton-card" />
        <div className="skeleton-card" />
      </div>
      <div className="skeleton-col">
        <div className="skeleton-line w-50" />
        <div className="skeleton-line w-30" />
        <div className="skeleton-block" />
      </div>
    </div>
  );
}
