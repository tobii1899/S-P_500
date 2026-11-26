/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)", surface: "var(--surface)", soft: "var(--soft)", line: "var(--line)", ink: "var(--ink)",
        muted: "var(--muted)", accent: "var(--accent)", win: "var(--win)", loss: "var(--loss)", idle: "var(--idle)",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", '"SF Pro Display"', '"Inter Variable"', '"Segoe UI"', "system-ui", "sans-serif"],
      },
      boxShadow: { card: "var(--shadow)" },
      borderRadius: { card: "20px" },
    },
  },
  plugins: [],
};
