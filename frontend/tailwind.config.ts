import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        background: "#080b10",
        foreground: "#e5e7eb",
        muted: "#111827",
        "muted-foreground": "#94a3b8",
        border: "#1f2937",
        card: "#0d1117",
        "card-foreground": "#e5e7eb",
        primary: "#38bdf8",
        "primary-foreground": "#03131d",
        destructive: "#ef4444",
        success: "#22c55e",
        warning: "#f59e0b"
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.04), 0 18px 48px rgba(0,0,0,0.22)"
      }
    }
  },
  plugins: []
};

export default config;
