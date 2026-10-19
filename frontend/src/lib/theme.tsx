/** Dark mode: "system" follows the OS; "light"/"dark" are remembered in localStorage. */
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export type ThemeMode = 'system' | 'light' | 'dark';

type ThemeValue = { mode: ThemeMode; setMode: (m: ThemeMode) => void; isDark: boolean };

const ThemeContext = createContext<ThemeValue>({
  mode: 'system',
  setMode: () => {},
  isDark: false,
});

function stored(): ThemeMode {
  try {
    const v = localStorage.getItem('theme');
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

function systemDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(stored);
  const [osDark, setOsDark] = useState(systemDark);

  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setOsDark(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const isDark = mode === 'dark' || (mode === 'system' && osDark);
  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark);
  }, [isDark]);

  const setMode = (m: ThemeMode) => {
    setModeState(m);
    try {
      if (m === 'system') localStorage.removeItem('theme');
      else localStorage.setItem('theme', m);
    } catch {
      /* not remembered */
    }
  };

  return (
    <ThemeContext.Provider value={{ mode, setMode, isDark }}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeValue {
  return useContext(ThemeContext);
}
