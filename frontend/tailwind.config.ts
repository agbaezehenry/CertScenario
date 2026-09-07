import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#0b1020", 900: "#111827", 800: "#1f2937", 700: "#374151", 600: "#4b5563", 500: "#6b7280", 400: "#9ca3af", 300: "#d1d5db", 200: "#e5e7eb", 100: "#f3f4f6", 50: "#f9fafb" },
        brand: { 600: "#1d4ed8", 500: "#2563eb", 400: "#3b82f6", 100: "#dbeafe" },
        ok: "#16a34a",
        warn: "#d97706",
        crit: "#dc2626",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
