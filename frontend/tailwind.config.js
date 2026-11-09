/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        "surface-muted": "var(--surface-muted)",
        line: "var(--line)",
        ink: "var(--ink)",
        muted: "var(--muted)",
        teal: "var(--teal)",
        "teal-soft": "var(--teal-soft)",
        blue: "var(--blue)",
        "blue-soft": "var(--blue-soft)",
        amber: "var(--amber)",
        "amber-soft": "var(--amber-soft)",
        danger: "var(--danger)",
        "danger-soft": "var(--danger-soft)",
        positive: "var(--positive)",
        "positive-soft": "var(--positive-soft)",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Inter",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      boxShadow: {
        card: "0 1px 2px rgba(31, 42, 46, 0.04), 0 8px 24px -18px rgba(31, 42, 46, 0.35)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 160ms ease-out",
        "slide-up": "slide-up 180ms ease-out",
      },
    },
  },
  plugins: [],
};
