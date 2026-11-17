import typography from '@tailwindcss/typography';

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        studio: {
          950: '#07090e',
          900: '#0d111a',
          800: '#151c2c',
          700: '#1f2940',
          accent: '#00f5d4',
          neon: '#7b2cbf',
          amber: '#ffbe0b',
        },
      },
      fontFamily: {
        sans: [
          '"Inter"',
          '"Hiragino Kaku Gothic ProN"',
          '"Noto Sans JP"',
          'sans-serif',
        ],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
    },
  },
  plugins: [typography],
};
