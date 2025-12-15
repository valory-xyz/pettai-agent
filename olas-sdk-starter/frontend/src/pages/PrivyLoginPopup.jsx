import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useLoginWithEmail, usePrivy } from '@privy-io/react-auth';
import { getOriginAliases } from '../utils/originAliases';

const STATUS = {
  INITIALIZING: 'initializing',
  PROMPTING: 'prompting',
  SENDING_CODE: 'sending-code',
  AWAITING_CODE: 'awaiting-code',
  VERIFYING_CODE: 'verifying-code',
  SENDING: 'sending',
  ERROR: 'error',
  COMPLETED: 'completed',
  NO_OPENER: 'no-opener',
};

const PrivyLoginPopupContent = () => {
  const { ready, authenticated, getAccessToken, logout } = usePrivy();
  const { sendCode, loginWithCode } = useLoginWithEmail();
  const [status, setStatus] = useState(STATUS.INITIALIZING);
  const [errorMessage, setErrorMessage] = useState(null);
  const [email, setEmail] = useState('');
  const [emailWithCode, setEmailWithCode] = useState(null);
  const [code, setCode] = useState('');
  const [isClearingSession, setIsClearingSession] = useState(false);
  const [hasForcedLogout, setHasForcedLogout] = useState(false);
  const forceLogout = useMemo(() => {
    if (typeof window === 'undefined') return false;
    try {
      const params = new URLSearchParams(window.location.search);
      const raw = params.get('forceLogout');
      if (raw === null) return false;
      const normalized = raw.toString().toLowerCase();
      return raw === '' || normalized === '1' || normalized === 'true';
    } catch (error) {
      console.warn('[PrivyLoginPopup] Unable to parse query params for forceLogout', error);
      return false;
    }
  }, []);
  const openerOrigins = useMemo(() => {
    if (typeof window === 'undefined') {
      return [];
    }
    const candidateOrigins = new Set();
    if (typeof document !== 'undefined' && document.referrer) {
      try {
        const refOrigin = new URL(document.referrer).origin;
        if (refOrigin) {
          candidateOrigins.add(refOrigin);
        }
      } catch (error) {
        console.warn('[PrivyLoginPopup] Unable to parse referrer origin', error);
      }
    }
    if (window.opener && !window.opener.closed) {
      try {
        if (window.opener.location?.origin) {
          candidateOrigins.add(window.opener.location.origin);
        }
      } catch (_error) {
        // Access to opener location can throw for cross-origin windows.
      }
    }
    candidateOrigins.add(window.location.origin);

    const aliasSet = new Set();
    candidateOrigins.forEach(origin => {
      if (!origin) return;
      getOriginAliases(origin).forEach(alias => aliasSet.add(alias));
    });

    return Array.from(aliasSet);
  }, []);
  const statusCopy = useMemo(
    () => ({
      [STATUS.INITIALIZING]: 'Preparing secure login…',
      [STATUS.PROMPTING]: 'Enter your email to receive a secure login code.',
      [STATUS.SENDING_CODE]: 'Sending a secure login code…',
      [STATUS.AWAITING_CODE]: 'Enter the code from your email to continue.',
      [STATUS.VERIFYING_CODE]: 'Verifying your code…',
      [STATUS.SENDING]: 'Securing your session…',
      [STATUS.ERROR]:
        errorMessage || 'Something went wrong. Close this window and retry.',
      [STATUS.NO_OPENER]:
        errorMessage ||
        'Could not detect the Pett Agent dashboard. Close this window and retry.',
      [STATUS.COMPLETED]: 'Login successful. You can close this tab.',
    }),
    [errorMessage]
  );
  const sendMessageToOpener = useCallback(
    payload => {
      if (typeof window === 'undefined') {
        return false;
      }
      if (!window.opener || window.opener.closed) {
        return false;
      }
      const timestampedPayload = { ...payload, sentAt: Date.now() };
      let delivered = false;

      openerOrigins.forEach(origin => {
        try {
          window.opener.postMessage(timestampedPayload, origin);
          delivered = true;
        } catch (error) {
          console.warn(
            `[PrivyLoginPopup] Failed to notify opener for origin ${origin}`,
            error
          );
        }
      });

      return delivered;
    },
    [openerOrigins]
  );

  const handlePrivyError = useCallback(
    (fallbackMessage, error) => {
      const details =
        error?.message ||
        error?.response?.message ||
        fallbackMessage ||
        'Unable to complete Privy login.';
      console.error('[PrivyLoginPopup] Privy error:', error || fallbackMessage);
      setErrorMessage(details);
      setStatus(STATUS.ERROR);
      sendMessageToOpener({
        type: 'privy-popup-error',
        message: details,
        error: {
          message: details,
          code: error?.code || error?.status,
          status: error?.status,
          stack: error?.stack,
        },
      });
    },
    [sendMessageToOpener]
  );

  useEffect(() => {
    return () => {
      sendMessageToOpener({
        type: 'privy-popup-closed',
        message: 'Login window closed.',
      });
    };
  }, [sendMessageToOpener]);

  const normalizedEmail = useMemo(() => email.trim().toLowerCase(), [email]);
  const hasCodeForCurrentEmail =
    normalizedEmail.length > 0 && emailWithCode === normalizedEmail;

  useEffect(() => {
    if (!hasCodeForCurrentEmail && status === STATUS.AWAITING_CODE) {
      setStatus(STATUS.PROMPTING);
      setCode('');
    }
  }, [hasCodeForCurrentEmail, status]);

  useEffect(() => {
    if (ready && !authenticated && status === STATUS.INITIALIZING) {
      setStatus(STATUS.PROMPTING);
    }
  }, [ready, authenticated, status]);

  useEffect(() => {
    if (!ready || !forceLogout || hasForcedLogout) return;
    const clearPrivySession = async () => {
      setIsClearingSession(true);
      try {
        await logout();
      } catch (error) {
        console.warn('[PrivyLoginPopup] Failed to clear Privy session before login', error);
      } finally {
        setHasForcedLogout(true);
        setIsClearingSession(false);
        setStatus(STATUS.PROMPTING);
        setErrorMessage(null);
      }
    };
    clearPrivySession();
  }, [forceLogout, hasForcedLogout, logout, ready]);

  const handleSendCode = useCallback(async () => {
    if (!normalizedEmail) {
      setErrorMessage('Enter a valid email address.');
      return;
    }
    try {
      setErrorMessage(null);
      setStatus(STATUS.SENDING_CODE);
      await sendCode({ email: normalizedEmail });
      setEmailWithCode(normalizedEmail);
      setCode('');
      setStatus(STATUS.AWAITING_CODE);
    } catch (error) {
      console.error('[PrivyLoginPopup] Failed to send code:', error);
      setErrorMessage(
        error?.message || 'Unable to send login code. Please try again.'
      );
      setStatus(STATUS.PROMPTING);
    }
  }, [normalizedEmail, sendCode]);

  const handleVerifyCode = useCallback(async () => {
    if (!code.trim()) {
      setErrorMessage('Enter the code from your email.');
      return;
    }
    try {
      setErrorMessage(null);
      setStatus(STATUS.VERIFYING_CODE);
      await loginWithCode({ code: code.trim() });
    } catch (error) {
      console.error('[PrivyLoginPopup] Failed to verify code:', error);
      setErrorMessage(
        error?.message ||
        'The code was invalid. Request a new code and try again.'
      );
      setStatus(STATUS.AWAITING_CODE);
    }
  }, [code, loginWithCode]);

  useEffect(() => {
    const sendToken = async () => {
      if (!window.opener || window.opener.closed) {
        setStatus(STATUS.NO_OPENER);
        setErrorMessage(
          'This window was opened without the Pett Agent dashboard. Please close it and try again.'
        );
        sendMessageToOpener({
          type: 'privy-popup-error',
          message: 'Login window lost reference to Pett dashboard.',
          error: {
            message:
              'This window was opened without the Pett Agent dashboard. Please close it and try again.',
          },
        });
        return;
      }
      try {
        setStatus(STATUS.SENDING);
        const token = await getAccessToken();
        if (!token) {
          throw new Error('Missing Privy token');
        }
        const delivered = sendMessageToOpener({
          type: 'privy-token',
          token,
        });
        if (!delivered) {
          console.warn(
            '[PrivyLoginPopup] Privy token dispatched but no opener origins accepted the message.'
          );
        }
        setStatus(STATUS.COMPLETED);
        window.close();
        sendMessageToOpener({
          type: 'privy-popup-status',
          status: STATUS.COMPLETED,
          message: statusCopy[STATUS.COMPLETED],
        });
      } catch (error) {
        handlePrivyError(
          'Unable to retrieve Privy token. Please try again.',
          error
        );
        await logout().catch(() => null);
      }
    };

    const shouldWaitForLogout =
      forceLogout && (isClearingSession || !hasForcedLogout);
    if (ready && authenticated && !shouldWaitForLogout) {
      sendToken();
    }
  }, [authenticated, forceLogout, getAccessToken, handlePrivyError, hasForcedLogout, isClearingSession, logout, ready, sendMessageToOpener, statusCopy]);

  const statusDescription = statusCopy[status];

  useEffect(() => {
    sendMessageToOpener({
      type: 'privy-popup-status',
      status,
      message: statusDescription,
      error:
        status === STATUS.ERROR
          ? {
            message: errorMessage || statusDescription,
          }
          : undefined,
    });
  }, [status, statusDescription, errorMessage, sendMessageToOpener]);

  const handleClose = useCallback(() => {
    sendMessageToOpener({
      type: 'privy-popup-closed',
      message: 'Login window closed by user.',
    });
    window.close();
  }, [sendMessageToOpener]);

  const showForm =
    status === STATUS.PROMPTING ||
    status === STATUS.SENDING_CODE ||
    status === STATUS.AWAITING_CODE ||
    status === STATUS.VERIFYING_CODE;
  const isSendingCode = status === STATUS.SENDING_CODE;
  const isVerifyingCode = status === STATUS.VERIFYING_CODE;
  const codeStepActive =
    hasCodeForCurrentEmail &&
    (status === STATUS.AWAITING_CODE || status === STATUS.VERIFYING_CODE);
  const disableInputs =
    status === STATUS.SENDING ||
    status === STATUS.COMPLETED ||
    status === STATUS.ERROR ||
    status === STATUS.NO_OPENER;
  const canSendCode =
    normalizedEmail.length > 0 && !isSendingCode && !isVerifyingCode && !disableInputs;
  const canVerifyCode =
    codeStepActive && code.trim().length > 0 && !isVerifyingCode && !disableInputs;
  const sendButtonLabel = hasCodeForCurrentEmail ? 'Resend code' : 'Send code';
  const showInlineError =
    !!errorMessage && status !== STATUS.ERROR && status !== STATUS.NO_OPENER;
  const codeTargetEmail = hasCodeForCurrentEmail ? normalizedEmail : emailWithCode;

  const handleUseDifferentEmail = useCallback(() => {
    if (disableInputs) return;
    setEmail('');
    setEmailWithCode(null);
    setCode('');
    setStatus(STATUS.PROMPTING);
    setErrorMessage(null);
  }, [disableInputs]);

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
        position: 'relative',
      }}
    >
      <button
        type="button"
        onClick={handleClose}
        aria-label="Close"
        style={{
          position: 'absolute',
          top: '1.25rem',
          right: '1.25rem',
          width: '2.25rem',
          height: '2.25rem',
          borderRadius: '999px',
          border: '1px solid rgba(255,255,255,0.3)',
          background: 'rgba(11,15,38,0.65)',
          color: '#f1f5ff',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
        }}
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
          <path fill="currentColor" d="M12 2c5.53 0 10 4.47 10 10s-4.47 10-10 10S2 17.53 2 12S6.47 2 12 2m3.59 5L12 10.59L8.41 7L7 8.41L10.59 12L7 15.59L8.41 17L12 13.41L15.59 17L17 15.59L13.41 12L17 8.41z" />
        </svg>
      </button>
      <div>
        <h1 style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>
          Pett Agent Login
        </h1>
        <p style={{ color: 'rgba(255,255,255,0.7)' }}>{statusDescription}</p>
      </div>
      {showForm && (
        <div
          style={{
            width: '100%',
            maxWidth: '24rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.75rem',
            textAlign: 'left',
          }}
        >
          <label
            htmlFor="privy-email"
            style={{ fontSize: '0.85rem', fontWeight: 600, color: '#9fb9ff' }}
          >
            Email address
          </label>
          <input
            id="privy-email"
            type="email"
            value={email}
            autoComplete="email"
            placeholder="you@example.com"
            disabled={isSendingCode || isVerifyingCode || disableInputs}
            onChange={event => {
              setEmail(event.target.value);
              if (showInlineError) {
                setErrorMessage(null);
              }
            }}
            onKeyDown={event => {
              if (event.key === 'Enter' && canSendCode) {
                event.preventDefault();
                handleSendCode();
              }
            }}
            style={{
              width: '100%',
              padding: '0.75rem 1rem',
              borderRadius: '0.75rem',
              border: '1px solid rgba(255,255,255,0.18)',
              background: 'rgba(15,20,48,0.85)',
              color: '#f8fbff',
              fontSize: '0.95rem',
              outline: 'none',
            }}
          />
          <button
            type="button"
            onClick={handleSendCode}
            disabled={!canSendCode}
            style={{
              padding: '0.75rem 1rem',
              borderRadius: '999px',
              border: 'none',
              background: canSendCode ? '#4A90E2' : 'rgba(255,255,255,0.12)',
              color: canSendCode ? '#fff' : 'rgba(255,255,255,0.5)',
              fontWeight: 600,
              cursor: canSendCode ? 'pointer' : 'not-allowed',
              transition: 'background 0.2s ease',
            }}
          >
            {isSendingCode ? 'Sending code…' : sendButtonLabel}
          </button>
          {codeStepActive && (
            <>
              <label
                htmlFor="privy-code"
                style={{ fontSize: '0.85rem', fontWeight: 600, color: '#9fb9ff', marginTop: '0.5rem' }}
              >
                Verification code
              </label>
              <input
                id="privy-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                placeholder="XXXXXX"
                disabled={isVerifyingCode || disableInputs}
                onChange={event => {
                  setCode(event.target.value);
                  if (showInlineError) {
                    setErrorMessage(null);
                  }
                }}
                onKeyDown={event => {
                  if (event.key === 'Enter' && canVerifyCode) {
                    event.preventDefault();
                    handleVerifyCode();
                  }
                }}
                style={{
                  width: '100%',
                  padding: '0.75rem 1rem',
                  borderRadius: '0.75rem',
                  border: '1px solid rgba(255,255,255,0.18)',
                  background: 'rgba(15,20,48,0.85)',
                  color: '#f8fbff',
                  fontSize: '1.05rem',
                  letterSpacing: '0.2rem',
                  textAlign: 'center',
                  outline: 'none',
                }}
              />
              <button
                type="button"
                onClick={handleVerifyCode}
                disabled={!canVerifyCode}
                style={{
                  padding: '0.75rem 1rem',
                  borderRadius: '999px',
                  border: 'none',
                  background: canVerifyCode ? '#58f0a7' : 'rgba(255,255,255,0.12)',
                  color: canVerifyCode ? '#081027' : 'rgba(255,255,255,0.6)',
                  fontWeight: 700,
                  cursor: canVerifyCode ? 'pointer' : 'not-allowed',
                  transition: 'background 0.2s ease',
                }}
              >
                {isVerifyingCode ? 'Verifying…' : 'Connect to Pett'}
              </button>
              {codeTargetEmail && (
                <p style={{ fontSize: '0.85rem', color: 'rgba(255,255,255,0.7)' }}>
                  A code was sent to <strong>{codeTargetEmail}</strong>. Enter it above to continue.
                </p>
              )}
              {!isVerifyingCode && !disableInputs && (
                <button
                  type="button"
                  onClick={handleUseDifferentEmail}
                  style={{
                    alignSelf: 'flex-start',
                    background: 'transparent',
                    border: 'none',
                    color: '#9fb9ff',
                    cursor: 'pointer',
                    fontSize: '0.85rem',
                    textDecoration: 'underline',
                    padding: 0,
                  }}
                >
                  Use a different email
                </button>
              )}
            </>
          )}
          {showInlineError && (
            <p style={{ color: '#feb2b2', fontSize: '0.85rem' }}>{errorMessage}</p>
          )}
        </div>
      )}
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
