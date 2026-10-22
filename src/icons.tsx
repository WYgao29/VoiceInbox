export function NavIcon({ name }: { name: "today" | "recordings" | "vocab" | "settings" }) {
  const common = { viewBox: "0 0 16 16", width: 16, height: 16, fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "today") {
    return (
      <svg {...common}>
        <rect x="2.5" y="3.5" width="11" height="10" rx="1.5" />
        <path d="M2.5 6.5h11M5.5 2.5v2M10.5 2.5v2" />
      </svg>
    );
  }
  if (name === "recordings") {
    return (
      <svg {...common}>
        <path d="M3 8.5v-1M5.5 10V6M8 11.5V4.5M10.5 10V6M13 8.5v-1" />
      </svg>
    );
  }
  if (name === "vocab") {
    return (
      <svg {...common}>
        <path d="M3 3.5h7.5a2 2 0 0 1 2 2V13H5a2 2 0 0 0-2 2V3.5Z" />
        <path d="M5.5 6.5h5M5.5 9h3.5" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 2.5v1.2M8 12.3v1.2M2.5 8h1.2M12.3 8h1.2M4.1 4.1l.9.9M11 11l.9.9M11.9 4.1 11 5M5 11l-.9.9" />
    </svg>
  );
}

export function EmptyIcon({ name }: { name: "search" | "filter" | "mic" }) {
  const common = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "search") {
    return (
      <svg {...common}>
        <circle cx="11" cy="11" r="6" />
        <path d="m16 16 4 4" />
      </svg>
    );
  }
  if (name === "filter") {
    return (
      <svg {...common}>
        <path d="M4 6h16M7 12h10M10 18h4" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M6 11a6 6 0 0 0 12 0M12 17v4" />
    </svg>
  );
}
