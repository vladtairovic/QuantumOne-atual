import type { SVGProps } from "react";

/**
 * Ícones vetoriais consistentes (traço 1.6, grade 24px).
 * Substituem glifos de texto (▤, ⌁, ◷, ⚙ …), que variam de fonte para fonte
 * e desalinham rótulos e botões.
 */
const paths = {
  plus: <path d="M12 5v14M5 12h14" />,
  files: (
    <>
      <path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z" />
      <path d="M14 3.5V8h4.5M9 12.5h6M9 16h4" />
    </>
  ),
  folder: <path d="M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2.2h7.4a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  attach: <path d="m20 11.5-7.6 7.6a4.6 4.6 0 0 1-6.5-6.5l7.9-7.9a3 3 0 0 1 4.3 4.3l-7.9 7.9a1.5 1.5 0 0 1-2.1-2.1l7.2-7.2" />,
  diff: <path d="M12 4v8M8 8h8M8 19h8" />,
  circuit: (
    <>
      <path d="M3.5 8h4M12.5 8h8M3.5 16h8M16.5 16h4" />
      <rect x="7.5" y="5" width="5" height="6" rx="1.4" />
      <rect x="11.5" y="13" width="5" height="6" rx="1.4" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.6-4.6" />
    </>
  ),
  history: (
    <>
      <path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.6" />
      <path d="M4 4.5v4.1h4.1M12 8v4.4l3 1.8" />
    </>
  ),
  projects: (
    <>
      <rect x="3.5" y="4.5" width="7" height="7" rx="1.8" />
      <rect x="13.5" y="4.5" width="7" height="7" rx="1.8" />
      <rect x="3.5" y="14.5" width="7" height="5" rx="1.6" />
      <rect x="13.5" y="14.5" width="7" height="5" rx="1.6" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M6 18l1.6-1.6M16.4 7.6 18 6" />
    </>
  ),
  sparkle: <path d="M12 3.5c.6 4.3 2.2 5.9 6.5 6.5-4.3.6-5.9 2.2-6.5 6.5-.6-4.3-2.2-5.9-6.5-6.5 4.3-.6 5.9-2.2 6.5-6.5zM18.5 16c.2 1.5.8 2.1 2.3 2.3-1.5.2-2.1.8-2.3 2.3-.2-1.5-.8-2.1-2.3-2.3 1.5-.2 2.1-.8 2.3-2.3z" />,
  close: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  arrowUp: <path d="M12 19V5M6 11l6-6 6 6" />,
  database: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="2.6" />
      <path d="M5 6v6c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6M5 12v6c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6v-6" />
    </>
  ),
} as const;

export type IconName = keyof typeof paths;

export default function Icon({ name, size = 16, className, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={["ui-icon", className].filter(Boolean).join(" ")}
      {...rest}
    >
      {paths[name]}
    </svg>
  );
}
