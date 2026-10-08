import React, { useEffect, useRef, useState } from 'react';
import { ImageOff, Loader2 } from 'lucide-react';
import api from '../../services/api';
import { cn } from '../common/cn';

/**
 * Renders an authenticated image (chat attachment).
 *
 * The backend's `/api/v1/chatbot/projects/{id}/attachments/{file}` route
 * requires a Bearer token, which a plain <img src=...> can't supply.
 * We fetch the bytes through the axios instance (which injects auth headers)
 * and turn the blob into an object URL.
 */
const AuthImage = ({ src, alt = '', className, onClick }) => {
  const [blobUrl, setBlobUrl] = useState(null);
  const [state, setState] = useState('loading'); // loading | ready | error
  const lastBlobRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    if (!src) {
      setState('error');
      return;
    }
    setState('loading');

    // Strip the `/api/v1` prefix because our axios baseURL already includes it.
    const path = src.startsWith('/api/v1')
      ? src.slice('/api/v1'.length)
      : src;

    // Use api.get (not fetch) so the Authorization header is attached.
    // The response interceptor unwraps to data; with responseType: 'blob' that
    // data is a Blob.
    api
      .get(path, { responseType: 'blob' })
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        lastBlobRef.current = url;
        setBlobUrl(url);
        setState('ready');
      })
      .catch(() => {
        if (cancelled) return;
        setState('error');
      });

    return () => {
      cancelled = true;
      if (lastBlobRef.current) {
        URL.revokeObjectURL(lastBlobRef.current);
        lastBlobRef.current = null;
      }
    };
  }, [src]);

  if (state === 'loading') {
    return (
      <div
        className={cn(
          'flex items-center justify-center rounded-lg bg-surface-muted/60 border border-line text-ink-subtle',
          className
        )}
        style={{ minHeight: 80 }}
      >
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }

  if (state === 'error' || !blobUrl) {
    return (
      <div
        className={cn(
          'flex items-center justify-center gap-2 rounded-lg bg-surface-muted/60 border border-line text-ink-subtle text-xs px-3 py-3',
          className
        )}
      >
        <ImageOff className="h-3.5 w-3.5" />
        <span>Image unavailable</span>
      </div>
    );
  }

  return (
    <img
      src={blobUrl}
      alt={alt}
      onClick={onClick}
      loading="lazy"
      className={cn(
        'block rounded-lg border border-line max-w-full h-auto',
        onClick && 'cursor-zoom-in',
        className
      )}
    />
  );
};

export default AuthImage;
