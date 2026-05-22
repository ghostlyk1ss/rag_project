import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#eef2ff", 100: "#e0e7ff",
          200: "#c7d2fe", 300: "#a5b4fc",
          400: "#818cf8", 500: "#6366f1",
          600: "#4f46e5", 700: "#4338ca",
          800: "#3730a3", 900: "#312e81",
        },
        surface: {
          DEFAULT: "#ffffff",
          secondary: "#f9fafb",
          tertiary: "#f3f4f6",
          glass: "rgba(255, 255, 255, 0.7)",
        },
        // 文字层级
        ink: {
          heading: "#1f2937",
          body: "#4b5563",
          muted: "#9ca3af",
        },
      },
      fontFamily: {
        sans: [
          "Inter", "-apple-system", "BlinkMacSystemFont",
          "Segoe UI", "Noto Sans SC", "PingFang SC",
          "system-ui", "sans-serif",
        ],
        mono: [
          "JetBrains Mono", "SF Mono", "Fira Code",
          "monospace",
        ],
      },
      borderRadius: {
        xl: "0.75rem",
        "2xl": "1rem",
        "3xl": "1.25rem",
        "4xl": "1.5rem",
        "button": "12px",
        "bubble-user": "16px 16px 4px 16px",
        "bubble-ai": "16px 16px 16px 4px",
        "input": "16px",
      },
      boxShadow: {
        subtle: "0 1px 2px 0 rgb(0 0 0 / 0.03)",
        card: "0 1px 3px 0 rgb(0 0 0 / 0.04), 0 1px 2px -1px rgb(0 0 0 / 0.06)",
        "card-hover": "0 4px 6px -1px rgb(0 0 0 / 0.05), 0 2px 4px -2px rgb(0 0 0 / 0.05)",
        popup: "0 10px 15px -3px rgb(0 0 0 / 0.06), 0 4px 6px -4px rgb(0 0 0 / 0.08)",
        "glass": "0 8px 32px rgba(0, 0, 0, 0.04), 0 1px 3px rgba(0, 0, 0, 0.02)",
        "glass-lg": "0 16px 48px rgba(0, 0, 0, 0.06), 0 2px 6px rgba(0, 0, 0, 0.03)",
        "elevated": "0 20px 60px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04)",
        "focus": "0 0 0 3px rgba(99, 102, 241, 0.2)",
      },
      backdropBlur: {
        glass: "16px",
      },
      animation: {
        "fade-in": "fadeIn 0.2s ease-out",
        "fade-in-up": "fadeInUp 0.25s ease-out",
        "slide-up": "slideUp 0.2s ease-out",
        "pulse-dot": "pulseDot 1.4s infinite ease-in-out",
        "spin-slow": "spin 3s linear infinite",
        "scale-in": "scaleIn 0.15s ease-out",
        "typing": "typing 1.4s infinite ease-in-out",
        "bounce-in": "bounceIn 0.3s ease-out",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        fadeInUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseDot: {
          "0%, 80%, 100%": { transform: "scale(0.8)", opacity: "0.3" },
          "40%": { transform: "scale(1)", opacity: "1" },
        },
        scaleIn: {
          "0%": { opacity: "0", transform: "scale(0.95)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        typing: {
          "0%, 80%, 100%": { transform: "translateY(0)", opacity: "0.3" },
          "40%": { transform: "translateY(-3px)", opacity: "1" },
        },
        bounceIn: {
          "0%": { opacity: "0", transform: "scale(0.9)" },
          "50%": { transform: "scale(1.02)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
