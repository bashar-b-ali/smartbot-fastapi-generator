/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./public/index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eef4ff',
          100: '#dae6ff',
          200: '#bdd2ff',
          300: '#91b3ff',
          400: '#5e88fb',
          500: '#3a63f5',
          600: '#2545e6',
          700: '#1d34c6',
          800: '#1c2ea0',
          900: '#1c2c7e',
        },
        accent: {
          50:  '#fdf4ff',
          100: '#fae8ff',
          200: '#f5d0fe',
          300: '#f0abfc',
          400: '#e879f9',
          500: '#d946ef',
          600: '#c026d3',
          700: '#a21caf',
          800: '#86198f',
          900: '#701a75',
        },
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          muted:   'rgb(var(--surface-muted) / <alpha-value>)',
          raised:  'rgb(var(--surface-raised) / <alpha-value>)',
        },
        ink: {
          DEFAULT:  'rgb(var(--ink) / <alpha-value>)',
          muted:    'rgb(var(--ink-muted) / <alpha-value>)',
          subtle:   'rgb(var(--ink-subtle) / <alpha-value>)',
          inverted: 'rgb(var(--ink-inverted) / <alpha-value>)',
        },
        line: {
          DEFAULT: 'rgb(var(--line) / <alpha-value>)',
          strong:  'rgb(var(--line-strong) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      borderRadius: {
        xl: '0.875rem',
        '2xl': '1.125rem',
        '3xl': '1.5rem',
      },
      // Layered shadow stack that mimics natural light: a tight 1px shadow plus
      // a wider, softer ambient halo. Reads as "elevation" rather than a flat drop.
      boxShadow: {
        card: '0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.04)',
        soft: '0 1px 2px 0 rgb(15 23 42 / 0.05), 0 8px 20px -6px rgb(15 23 42 / 0.10)',
        pop:  '0 2px 4px -1px rgb(15 23 42 / 0.06), 0 18px 40px -10px rgb(15 23 42 / 0.18)',
        lift: '0 1px 2px rgb(15 23 42 / 0.04), 0 12px 28px -8px rgb(15 23 42 / 0.14), 0 4px 8px -4px rgb(15 23 42 / 0.04)',
        glow: '0 0 0 4px rgb(58 99 245 / 0.15)',
        'glow-primary': '0 0 24px -4px rgb(58 99 245 / 0.45)',
        'glow-accent':  '0 0 24px -4px rgb(217 70 239 / 0.45)',
        'inner-line':   'inset 0 0 0 1px rgb(15 23 42 / 0.06)',
      },
      backgroundImage: {
        'grid-light': "radial-gradient(circle at 1px 1px, rgba(15,23,42,0.06) 1px, transparent 0)",
        'grid-dark':  "radial-gradient(circle at 1px 1px, rgba(255,255,255,0.06) 1px, transparent 0)",
        'mesh': "radial-gradient(at 20% 20%, rgba(59,99,245,0.18) 0, transparent 50%), radial-gradient(at 80% 30%, rgba(217,70,239,0.18) 0, transparent 50%), radial-gradient(at 60% 80%, rgba(56,189,248,0.18) 0, transparent 50%)",
        'sheen': "linear-gradient(110deg, transparent 35%, rgba(255,255,255,0.18) 50%, transparent 65%)",
      },
      animation: {
        'fade-in':    'fadeIn .4s cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-up':   'slideUp .45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-down': 'slideDown .45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'pop-in':     'popIn .28s cubic-bezier(0.16, 1, 0.3, 1) both',
        'shimmer':    'shimmer 1.8s linear infinite',
        'blob':       'blob 18s ease-in-out infinite',
        'breathe':    'breathe 6s ease-in-out infinite',
        'float':      'float 8s ease-in-out infinite',
        'sheen':      'sheen 2.4s cubic-bezier(0.4, 0, 0.2, 1) infinite',
      },
      keyframes: {
        fadeIn:    { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
        slideUp:   { '0%': { transform: 'translateY(10px)', opacity: 0 }, '100%': { transform: 'translateY(0)', opacity: 1 } },
        slideDown: { '0%': { transform: 'translateY(-10px)', opacity: 0 }, '100%': { transform: 'translateY(0)', opacity: 1 } },
        popIn:     { '0%': { transform: 'scale(.95)', opacity: 0 }, '100%': { transform: 'scale(1)', opacity: 1 } },
        shimmer:   { '0%': { backgroundPosition: '-400px 0' }, '100%': { backgroundPosition: '400px 0' } },
        blob: {
          '0%, 100%': { transform: 'translate(0,0) scale(1)' },
          '33%':      { transform: 'translate(30px,-20px) scale(1.05)' },
          '66%':      { transform: 'translate(-20px,20px) scale(.95)' },
        },
        breathe: {
          '0%, 100%': { transform: 'scale(1)', opacity: 0.85 },
          '50%':      { transform: 'scale(1.04)', opacity: 1 },
        },
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%':      { transform: 'translateY(-6px)' },
        },
        sheen: {
          '0%':   { transform: 'translateX(-120%)' },
          '100%': { transform: 'translateX(120%)' },
        },
      },
      transitionTimingFunction: {
        'spring':   'cubic-bezier(0.2, 0.8, 0.2, 1)',
        'out-soft': 'cubic-bezier(0.16, 1, 0.3, 1)',
        'apple':    'cubic-bezier(0.32, 0.72, 0, 1)',
      },
      transitionDuration: {
        250: '250ms',
        350: '350ms',
        450: '450ms',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
};
