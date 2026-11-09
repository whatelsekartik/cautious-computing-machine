/** Small hand-drawn accents. Used sparingly: a squiggle here, an arrow there. */

export function Squiggle({ className = "", width = 96 }: { className?: string; width?: number }) {
  return (
    <svg
      width={width}
      height="8"
      viewBox="0 0 96 8"
      fill="none"
      aria-hidden="true"
      className={className}
      preserveAspectRatio="none"
    >
      <path
        d="M1 5.4C8 2.1 13.5 6.6 21 4.4c7-2 11.4 2.9 19 .9 7.2-1.9 11.3 2.4 18.4.5 7.4-2 12 2.3 19.1.2 6.6-2 11.8 1.4 17.5-.6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function SketchCircle({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 120 60" fill="none" aria-hidden="true" className={className}>
      <path
        d="M52 4C24 5 5 16 7 30c2 15 30 26 58 25 26-1 48-12 47-26C111 15 90 4 63 3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        opacity="0.55"
      />
      <path
        d="M78 6C101 8 116 17 114 29c-2 13-27 24-52 25C38 55 12 46 10 32"
        stroke="currentColor"
        strokeWidth="1"
        strokeLinecap="round"
        opacity="0.25"
      />
    </svg>
  );
}

export function DoodleArrow({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 70 42" fill="none" aria-hidden="true" className={className}>
      <path
        d="M3 6c9 15 21 24 38 27m0 0-9-9m9 9-11 5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect width="32" height="32" rx="8.5" fill="var(--teal)" />
      <path
        d="M7.5 20.2l4.4-7.6 3.9 9.6 3.2-6.2 5.5 3.1"
        stroke="#FAF8F3"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24.4" cy="9.4" r="2.2" fill="#FAF8F3" opacity="0.9" />
    </svg>
  );
}
