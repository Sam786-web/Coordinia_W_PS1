/** @type {import('tailwindcss').Config} */
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        bg: v('bg'), // page behind the app shell
        shell: v('shell'), // the app window
        panel: v('panel'), // analytics + task-row surface
        surface: v('surface'), // cards and rows inside panels
        subtle: v('subtle'),
        line: v('line'),
        'line-strong': v('line-strong'),
        fg: v('fg'),
        'fg-2': v('fg-2'),
        'fg-3': v('fg-3'),
        // Brand accent (coral). Kept under the "indigo" name so every existing class follows it.
        indigo: {
          300: v('accent-300'),
          400: v('accent-400'),
          500: v('accent-500'),
          600: v('accent-600'),
          700: v('accent-700'),
        },
        // Fixed pastels - identical in College and Corporate mode so they pop on white and black
        lavender: '#DDD6FE',
        butter: '#FEF08A',
        flamingo: '#FBCFE8',
        mint: '#BBF7D0',
        lime: '#D8F366',
        coral: '#FF6B4A',
        terracotta: '#E06348',
        capsule: '#F04E6E',
      },
      borderRadius: {
        shell: '36px',
        bento: '28px',
        stat: '24px',
        row: '20px',
        card: '28px',
        ctl: '9999px',
      },
      keyframes: {
        'fade-in': { '0%': { opacity: 0, transform: 'translateY(4px)' }, '100%': { opacity: 1, transform: 'none' } },
        highlight: {
          '0%': { boxShadow: '0 0 0 0 rgba(255,107,74,0)' },
          '20%': { boxShadow: '0 0 0 3px rgba(255,107,74,0.45)' },
          '100%': { boxShadow: '0 0 0 0 rgba(255,107,74,0)' },
        },
        float: { '0%, 100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-8px)' } },
        rise: { '0%': { opacity: 0, transform: 'translateY(14px) scale(.98)' }, '100%': { opacity: 1, transform: 'none' } },
        grow: { '0%': { transform: 'scaleY(0)' }, '100%': { transform: 'scaleY(1)' } },
      },
      animation: {
        'fade-in': 'fade-in .2s ease-out both',
        highlight: 'highlight 1.6s ease-out',
        float: 'float 6s ease-in-out infinite',
        'float-slow': 'float 8s ease-in-out infinite',
        rise: 'rise .7s cubic-bezier(.2,.8,.2,1) both',
        grow: 'grow 1s cubic-bezier(.2,.8,.2,1) both',
      },
    },
  },
  plugins: [],
}
