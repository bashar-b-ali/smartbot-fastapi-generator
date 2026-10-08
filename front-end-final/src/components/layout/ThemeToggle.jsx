import React from 'react';
import { Sun, Moon } from 'lucide-react';
import { useTheme } from '../../contexts/ThemeContext';
import IconButton from '../common/IconButton';

const ThemeToggle = ({ size = 'md' }) => {
  const { isDark, toggleTheme } = useTheme();
  return (
    <IconButton
      onClick={toggleTheme}
      label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      tone="subtle"
      size={size}
    >
      {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </IconButton>
  );
};

export default ThemeToggle;
