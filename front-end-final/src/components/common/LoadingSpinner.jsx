import React from 'react';
import { Spinner, FullPageSpinner } from './Spinner';

const LoadingSpinner = ({ size = 'large', label, fullscreen = true }) => {
  const map = { small: 'sm', medium: 'md', large: 'xl' };
  if (fullscreen) return <FullPageSpinner label={label} />;
  return <Spinner size={map[size] || size} label={label} />;
};

export default LoadingSpinner;
