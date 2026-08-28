// Minimal inline stroke icons (no external icon library dependency).
// All icons accept a `className` for sizing/coloring via Tailwind.

interface IconProps {
  className?: string;
}

const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  viewBox: "0 0 24 24",
};

export function IconPerson({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20c1.5-4 4.5-6 7.5-6s6 2 7.5 6" />
    </svg>
  );
}

export function IconPersonX({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <circle cx="10" cy="8" r="3.5" />
      <path d="M3 20c1.3-3.6 4-5.5 7-5.5s5.7 1.9 7 5.5" />
      <path d="M17 8l4 4M21 8l-4 4" />
    </svg>
  );
}

export function IconPersonCheck({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <circle cx="10" cy="8" r="3.5" />
      <path d="M3 20c1.3-3.6 4-5.5 7-5.5s5.7 1.9 7 5.5" />
      <path d="M16 12.5l2 2 3.5-3.5" />
    </svg>
  );
}

export function IconBuilding({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <rect x="4" y="3" width="12" height="18" rx="1" />
      <path d="M20 21V9h-4" />
      <path d="M7.5 7h1M7.5 11h1M7.5 15h1M11.5 7h1M11.5 11h1M11.5 15h1" />
    </svg>
  );
}

export function IconDeal({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M3 12l9-9 9 9-9 9-9-9z" />
      <circle cx="12" cy="12" r="2.25" />
    </svg>
  );
}

export function IconTicket({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1.5a1.75 1.75 0 0 0 0 3V15a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1.5a1.75 1.75 0 0 0 0-3V9z" />
      <path d="M10 7v10" strokeDasharray="2 2" />
    </svg>
  );
}

export function IconShield({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

export function IconCopy({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <rect x="9" y="9" width="11" height="11" rx="1.5" />
      <path d="M5 15V5a1 1 0 0 1 1-1h10" />
    </svg>
  );
}

export function IconLink({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M9 15l6-6" />
      <path d="M10.5 6.5l1-1a3.5 3.5 0 0 1 5 5l-1 1" />
      <path d="M13.5 17.5l-1 1a3.5 3.5 0 0 1-5-5l1-1" />
    </svg>
  );
}

export function IconWarning({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M12 3.5L21.5 20h-19L12 3.5z" />
      <path d="M12 10v4" />
      <path d="M12 17.2v.1" />
    </svg>
  );
}

export function IconChevronDown({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

export function IconRefresh({ className }: IconProps) {
  return (
    <svg className={className} {...base}>
      <path d="M4 12a8 8 0 0 1 14-5.3L20 8" />
      <path d="M20 4v4h-4" />
      <path d="M20 12a8 8 0 0 1-14 5.3L4 16" />
      <path d="M4 20v-4h4" />
    </svg>
  );
}
