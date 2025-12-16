/**
 * useAuthMessageListener.js
 * 
 * Add this hook to your AuthProvider to receive tokens from the popup
 * when running in an Electron iframe context.
 * 
 * Usage in your AuthProvider:
 * 
 * import { useAuthMessageListener } from './hooks/useAuthMessageListener';
 * 
 * // Inside your AuthProvider component:
 * useAuthMessageListener({
 *   onToken: (token) => {
 *     setPrivyToken(token);
 *     // ... rest of your token handling
 *   },
 *   onError: (error) => {
 *     setLoginError(error.message);
 *   },
 *   onPopupClosed: () => {
 *     setIsLoggingIn(false);
 *   },
 *   onStatusChange: (status, message) => {
 *     console.log('Popup status:', status, message);
 *   },
 * });
 */

import { useCallback, useEffect, useRef } from 'react';

export const useAuthMessageListener = ({
  onToken,
  onError,
  onPopupClosed,
  onStatusChange,
}) => {
  const broadcastChannelRef = useRef(null);
  const lastProcessedRef = useRef(0); // Prevent duplicate processing

  // Handle incoming auth messages
  const handleAuthMessage = useCallback((data) => {
    // Handle both MessageEvent and raw data
    if (data instanceof MessageEvent) {
      data = data.data;
    }

    // Parse if string
    if (typeof data === 'string') {
      try {
        data = JSON.parse(data);
      } catch (e) {
        return;
      }
    }

    if (!data || !data.type) return;

    // Deduplicate messages (same message might arrive via multiple channels)
    const messageId = data.sentAt || data._storageTimestamp || Date.now();
    if (messageId <= lastProcessedRef.current) {
      return; // Already processed this message
    }
    lastProcessedRef.current = messageId;

    console.log('[useAuthMessageListener] Received:', data.type);

    switch (data.type) {
      case 'privy-token':
        if (data.token) {
          console.log('[useAuthMessageListener] Token received!');
          onToken?.(data.token);
        }
        break;

      case 'privy-popup-error':
        console.error('[useAuthMessageListener] Popup error:', data.message);
        onError?.(data.error || { message: data.message });
        break;

      case 'privy-popup-closed':
        console.log('[useAuthMessageListener] Popup closed');
        onPopupClosed?.();
        break;

      case 'privy-popup-status':
        console.log('[useAuthMessageListener] Status:', data.status);
        onStatusChange?.(data.status, data.message);
        break;

      default:
        break;
    }
  }, [onToken, onError, onPopupClosed, onStatusChange]);

  // Handle postMessage events
  const handlePostMessage = useCallback((event) => {
    handleAuthMessage(event.data);
  }, [handleAuthMessage]);

  // Handle localStorage events (fires when OTHER windows change localStorage)
  const handleStorageChange = useCallback((event) => {
    if (event.key !== 'pett-auth-message') return;
    if (!event.newValue) return;

    try {
      const data = JSON.parse(event.newValue);
      handleAuthMessage(data);
    } catch (e) {
      console.warn('[useAuthMessageListener] Failed to parse localStorage:', e);
    }
  }, [handleAuthMessage]);

  // Set up all listeners
  useEffect(() => {
    console.log('[useAuthMessageListener] Setting up listeners...');

    // 1. PostMessage listener (standard)
    window.addEventListener('message', handlePostMessage);

    // 2. LocalStorage listener (same-origin fallback)
    window.addEventListener('storage', handleStorageChange);

    // 3. BroadcastChannel listener (MAIN METHOD for Electron)
    if (typeof BroadcastChannel !== 'undefined') {
      try {
        broadcastChannelRef.current = new BroadcastChannel('pett-auth-channel');
        broadcastChannelRef.current.onmessage = (event) => {
          handleAuthMessage(event.data);
        };
        console.log('[useAuthMessageListener] ✓ BroadcastChannel ready');
      } catch (e) {
        console.warn('[useAuthMessageListener] BroadcastChannel setup failed:', e);
      }
    }

    // 4. Also check localStorage on mount (in case message was sent before listener was ready)
    try {
      const pending = localStorage.getItem('pett-auth-message');
      if (pending) {
        const data = JSON.parse(pending);
        // Only process if recent (within last 5 seconds)
        const timestamp = data.sentAt || data._storageTimestamp || 0;
        if (Date.now() - timestamp < 5000) {
          handleAuthMessage(data);
        }
        localStorage.removeItem('pett-auth-message');
      }
    } catch (e) {
      // Ignore
    }

    return () => {
      console.log('[useAuthMessageListener] Cleaning up listeners...');
      window.removeEventListener('message', handlePostMessage);
      window.removeEventListener('storage', handleStorageChange);
      
      if (broadcastChannelRef.current) {
        try {
          broadcastChannelRef.current.close();
        } catch (e) {
          // Ignore
        }
      }
    };
  }, [handlePostMessage, handleStorageChange, handleAuthMessage]);
};

export default useAuthMessageListener;

