import React, { useEffect, useState } from 'react';
import { usePrivy } from '@privy-io/react-auth';

const STATUS = {
  INITIALIZING: 'initializing',
  PROMPTING: 'prompting',
  SENDING: 'sending',
  ERROR: 'error',
  COMPLETED: 'completed',
  NO_OPENER: 'no-opener',
};

const PrivyLoginPopupContent = () => {
  const { ready, login, authenticated, getAccessToken, logout } = usePrivy();
  const [status, setStatus] = useState(STATUS.INITIALIZING);
  const [errorMessage, setErrorMessage] = useState(null);

  useEffect(() => {
    return () => {
      try {
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage(
            { type: 'privy-popup-closed' },
            window.location.origin
          );
        }
      } catch (error) {
        console.warn('[PrivyLoginPopup] Failed to notify opener on close', error);
      }
    };
  }, []);

  useEffect(() => {
    if (ready && !authenticated) {
      setStatus(STATUS.PROMPTING);
      login();
    }
  }, [ready, authenticated, login]);

  useEffect(() => {
    const sendToken = async () => {
      if (!window.opener || window.opener.closed) {
        setStatus(STATUS.NO_OPENER);
        setErrorMessage(
          'This window was opened without the Pett Agent dashboard. Please close it and try again.'
        );
        return;
      }
      try {
        setStatus(STATUS.SENDING);
        const token = await getAccessToken();
        if (!token) {
          throw new Error('Missing Privy token');
        }
        window.opener.postMessage(
          { type: 'privy-token', token },
          window.location.origin
        );
        setStatus(STATUS.COMPLETED);
        window.close();
      } catch (error) {
        console.error('[PrivyLoginPopup] Failed to retrieve token:', error);
        setStatus(STATUS.ERROR);
        setErrorMessage(
          error?.message || 'Unable to retrieve Privy token. Please try again.'
        );
        await logout().catch(() => null);
      }
    };

    if (ready && authenticated) {
      sendToken();
    }
  }, [ready, authenticated, getAccessToken, logout]);

  const statusCopy = {
    [STATUS.INITIALIZING]: 'Preparing secure login…',
    [STATUS.PROMPTING]: 'Launching Privy login…',
    [STATUS.SENDING]: 'Securing your session…',
    [STATUS.ERROR]: errorMessage || 'Something went wrong. Close this window and retry.',
    [STATUS.NO_OPENER]:
      errorMessage ||
      'Could not detect the Pett Agent dashboard. Close this window and retry.',
    [STATUS.COMPLETED]: 'Login successful. You can close this tab.',
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '1rem',
        background: '#0b0f26',
        color: '#f1f5ff',
        textAlign: 'center',
        padding: '2rem',
      }}
    >
      <div>
        <h1 style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>
          Pett Agent Login
        </h1>
        <p style={{ color: 'rgba(255,255,255,0.7)' }}>{statusCopy[status]}</p>
      </div>
      {(status === STATUS.ERROR || status === STATUS.NO_OPENER) && (
        <button
          style={{
            padding: '0.6rem 1.25rem',
            borderRadius: '999px',
            border: 'none',
            background: '#4A90E2',
            color: '#fff',
            fontWeight: '600',
            cursor: 'pointer',
          }}
          onClick={() => {
            window.location.reload();
          }}
        >
          Retry
        </button>
      )}
    </div>
  );
};

const PrivyLoginPopup = () => {
  return <PrivyLoginPopupContent />;
};

export default PrivyLoginPopup;
