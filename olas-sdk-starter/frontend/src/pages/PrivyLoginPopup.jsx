import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { useLoginWithEmail, usePrivy } from '@privy-io/react-auth';

const PrivyLoginPopup = () => {
  const { ready, authenticated, getAccessToken, logout } = usePrivy();
  const { sendCode, loginWithCode } = useLoginWithEmail();

  const [status, setStatus] = useState('init'); // init, ready, sending-code, awaiting-code, verifying, sending-token, done, error
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState(null);
  const [hasCleared, setHasCleared] = useState(false);
  const hasSentToken = useRef(false);

  // Clear any existing Privy session on mount
  useEffect(() => {
    if (!ready || hasCleared) return;

    console.log('[Popup] Clearing Privy session...');
    const clear = async () => {
      try {
        await logout();
      } catch (e) {
        console.log('[Popup] Logout error (ok):', e);
      }
      console.log('[Popup] Session cleared, ready for login');
      setHasCleared(true);
      setStatus('ready');
    };
    clear();
  }, [ready, hasCleared, logout]);

  // Send token to parent when authenticated
  useEffect(() => {
    if (!ready || !authenticated || !hasCleared || hasSentToken.current) return;

    console.log('[Popup] Authenticated! Getting access token...');
    const send = async () => {
      setStatus('sending-token');
      hasSentToken.current = true;

      try {
        const token = await getAccessToken();
        if (!token) throw new Error('No token received');

        console.log('[Popup] Got token, sending to parent via BroadcastChannel...');

        // Send via BroadcastChannel - keep it open longer to ensure delivery
        const channel = new BroadcastChannel('pett-auth');
        channel.postMessage({ type: 'token', token });
        console.log('[Popup] Token sent via BroadcastChannel!');

        // Also try postMessage as backup (if opener still exists)
        try {
          if (window.opener && !window.opener.closed) {
            window.opener.postMessage({ type: 'token', token }, window.location.origin);
            console.log('[Popup] Token also sent via postMessage');
          }
        } catch (e) {
          console.log('[Popup] postMessage failed (expected in Electron):', e);
        }

        // Keep channel open longer to ensure delivery
        setTimeout(() => {
          channel.close();
          console.log('[Popup] Channel closed');
        }, 2000);

        setStatus('done');

        // Close popup after a delay
        setTimeout(() => {
          console.log('[Popup] Closing popup window...');
          window.close();
        }, 1000);
      } catch (e) {
        console.error('[Popup] Error getting/sending token:', e);
        setError(e?.message || 'Failed to get token');
        setStatus('error');
        hasSentToken.current = false;
      }
    };
    send();
  }, [ready, authenticated, hasCleared, getAccessToken]);

  const handleSendCode = useCallback(async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed) {
      setError('Enter your email');
      return;
    }

    setError(null);
    setStatus('sending-code');
    console.log('[Popup] Sending code to:', trimmed);

    try {
      await sendCode({ email: trimmed });
      setCode('');
      setStatus('awaiting-code');
      console.log('[Popup] Code sent successfully');
    } catch (e) {
      console.error('[Popup] Failed to send code:', e);
      setError(e?.message || 'Failed to send code');
      setStatus('ready');
    }
  }, [email, sendCode]);

  const handleVerifyCode = useCallback(async () => {
    const trimmed = code.trim();
    if (!trimmed) {
      setError('Enter the code');
      return;
    }

    setError(null);
    setStatus('verifying');
    console.log('[Popup] Verifying code...');

    try {
      await loginWithCode({ code: trimmed });
      console.log('[Popup] Code verified! Waiting for authenticated state...');
      // useEffect will handle the token sending when authenticated becomes true
    } catch (e) {
      console.error('[Popup] Code verification failed:', e);
      setError(e?.message || 'Invalid code');
      setStatus('awaiting-code');
    }
  }, [code, loginWithCode]);

  const handleClose = useCallback(() => {
    console.log('[Popup] User closed popup');
    const channel = new BroadcastChannel('pett-auth');
    channel.postMessage({ type: 'closed' });
    setTimeout(() => {
      channel.close();
      window.close();
    }, 100);
  }, []);

  const statusText = useMemo(() => {
    switch (status) {
      case 'init':
        return 'Preparing...';
      case 'ready':
        return 'Enter your email';
      case 'sending-code':
        return 'Sending code...';
      case 'awaiting-code':
        return 'Enter the code from your email';
      case 'verifying':
        return 'Verifying...';
      case 'sending-token':
        return 'Connecting...';
      case 'done':
        return 'Done! This window will close automatically.';
      case 'error':
        return error || 'Something went wrong';
      default:
        return '';
    }
  }, [status, error]);

  const showForm = ['ready', 'sending-code', 'awaiting-code', 'verifying'].includes(status);
  const showCodeInput = ['awaiting-code', 'verifying'].includes(status);
  const isLoading = ['init', 'sending-code', 'verifying', 'sending-token'].includes(status);

  return (
    <div style={styles.container}>
      <button onClick={handleClose} style={styles.closeBtn} aria-label="Close">
        ✕
      </button>

      <h1 style={styles.title}>Pett Agent Login</h1>
      <p style={styles.status}>{statusText}</p>

      {showForm && (
        <div style={styles.form}>
          <input
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !showCodeInput && handleSendCode()}
            disabled={isLoading}
            style={styles.input}
          />
          <button
            onClick={handleSendCode}
            disabled={isLoading || !email.trim()}
            style={{
              ...styles.button,
              opacity: isLoading || !email.trim() ? 0.5 : 1,
            }}
          >
            {status === 'sending-code' ? 'Sending...' : showCodeInput ? 'Resend code' : 'Send code'}
          </button>

          {showCodeInput && (
            <>
              <input
                type="text"
                placeholder="Enter code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleVerifyCode()}
                disabled={status === 'verifying'}
                style={{ ...styles.input, textAlign: 'center', letterSpacing: '0.2em' }}
              />
              <button
                onClick={handleVerifyCode}
                disabled={status === 'verifying' || !code.trim()}
                style={{
                  ...styles.button,
                  background: '#58f0a7',
                  color: '#081027',
                  opacity: status === 'verifying' || !code.trim() ? 0.5 : 1,
                }}
              >
                {status === 'verifying' ? 'Verifying...' : 'Connect'}
              </button>
            </>
          )}

          {error && <p style={styles.error}>{error}</p>}
        </div>
      )}

      {status === 'error' && (
        <button onClick={() => window.location.reload()} style={styles.button}>
          Retry
        </button>
      )}
    </div>
  );
};

const styles = {
  container: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '1rem',
    background: '#0b0f26',
    color: '#f1f5ff',
    padding: '2rem',
    textAlign: 'center',
    position: 'relative',
  },
  closeBtn: {
    position: 'absolute',
    top: '1rem',
    right: '1rem',
    width: '2rem',
    height: '2rem',
    borderRadius: '50%',
    border: '1px solid rgba(255,255,255,0.3)',
    background: 'transparent',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '1rem',
  },
  title: {
    fontSize: '1.5rem',
    margin: 0,
  },
  status: {
    color: 'rgba(255,255,255,0.7)',
    margin: 0,
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.75rem',
    width: '100%',
    maxWidth: '20rem',
  },
  input: {
    padding: '0.75rem 1rem',
    borderRadius: '0.5rem',
    border: '1px solid rgba(255,255,255,0.2)',
    background: 'rgba(255,255,255,0.1)',
    color: '#fff',
    fontSize: '1rem',
    outline: 'none',
  },
  button: {
    padding: '0.75rem 1rem',
    borderRadius: '2rem',
    border: 'none',
    background: '#4A90E2',
    color: '#fff',
    fontSize: '1rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
  error: {
    color: '#ff8080',
    fontSize: '0.875rem',
    margin: 0,
  },
};

export default PrivyLoginPopup;
