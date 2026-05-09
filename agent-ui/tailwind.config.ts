import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // User palette
        cream:    "#FFFFEB",
        green:    "#60DFB1",
        blue:     "#60A4DF",
        yellow:   "#DFD660",
        red:      "#DF6460",
        charcoal: "#0E0804",

        // Surface hierarchy (Claude Code dark-first)
        canvas:   "#0E0804",
        surface:  "#161310",
        card:     "#1E1A15",
        elevated: "#252018",
        bash:     "#0B0907",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "monospace"],
      },
      borderRadius: {
        sm: "6px",
        md: "8px",
        lg: "12px",
        pill: "9999px",
      },
      animation: {
        "shimmer-blue":   "shimmerBlue 1.4s ease-in-out infinite",
        "shimmer-yellow": "shimmerYellow 2s ease-in-out infinite",
        "fade-in":        "fadeIn 0.2s ease",
      },
      keyframes: {
        shimmerBlue: {
          "0%, 100%": { borderColor: "rgba(96,164,223,0.7)" },
          "50%":       { borderColor: "rgba(96,164,223,0.20)" },
        },
        shimmerYellow: {
          "0%, 100%": { borderColor: "rgba(223,214,96,0.7)" },
          "50%":       { borderColor: "rgba(223,214,96,0.20)" },
        },
        fadeIn: {
          from: { opacity: "0", transform: "translateY(4px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
