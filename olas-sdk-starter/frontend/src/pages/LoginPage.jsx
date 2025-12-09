import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePrivyModalHeight } from '../hooks/usePrivyModalHeight';
import { useAuth } from '../providers/AuthProvider';
import backgroundMain from '../assets/images/background-3.jpg';
import backgroundOverlay from '../assets/images/background-0.jpg';
import './LoginPage.scss';

const LoginPage = () => {
  const { login, isModalOpen, wsPet, authenticated } = useAuth();
  const hasCalledLogin = useRef(false);
  const privyModalHeight = usePrivyModalHeight();
  const navigate = useNavigate();

  useEffect(() => {
    if (!hasCalledLogin.current) {
      hasCalledLogin.current = true;
      login();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (wsPet !== null) {
      navigate('/all-set', { replace: true });
    }
  }, [wsPet, navigate]);

  useEffect(() => {
    if (!isModalOpen && hasCalledLogin.current && !authenticated) {
      login();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isModalOpen, authenticated]);

  if (authenticated && wsPet === null) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 text-center bg-gradient-to-b from-indigo-400/25 to-[#0e122c]">
        <div className="h-14 w-14 animate-spin rounded-full border-4 border-white/25 border-t-[#7c9bff]" />
        <p className="text-lg font-semibold text-white">Connecting to your Pett agent...</p>
        <p className="text-sm text-white/70">Hang tight while we link up the control room.</p>
      </div>
    );
  }

  const safeAreaStyle = {
    '--login-bg': `url(${backgroundMain})`,
    '--login-overlay': `url(${backgroundOverlay})`,
    '--privy-modal-height': privyModalHeight > 0 ? `${privyModalHeight}px` : '0px',
    paddingTop: 'calc(var(--safe-area-inset-top) + 4rem)',
    paddingBottom: 'calc(var(--safe-area-inset-bottom) + 2.5rem)',
  };

  return (
    <div className="login-portal" style={safeAreaStyle}>
      <div className="login-portal__background" />
      <div className="login-portal__overlay" />
      <div className="login-portal__glow login-portal__glow--primary" />
      <div className="login-portal__glow login-portal__glow--secondary" />


      <div className="login-portal__content">
        <div className="login-portal__hero">
          <p className="login-portal__pretitle">Welcome</p>
          <h1 className="login-portal__headline">
            Sync with your <span>Pett Agent</span>
          </h1>
          <p className="login-portal__copy">
            Launch the secure Privy login to connect with your agent. Once authenticated, we&apos;ll take you
            straight into the control room.
          </p>
        </div>
      </div>

      {/* <footer className="login-portal__footer">
        You can sign in at <strong>app.pett.ai</strong> even if you created your agent with Pearl. Once you finish,
        this window will confirm the connection automatically.
      </footer> */}

      <style>{`
        .login-portal {
          position: relative;
          min-height: 100vh;
          width: 100%;
          overflow: hidden;
          background: #0b0f26;
          display: flex;
          flex-direction: column;
        }

        .login-portal__background {
          position: absolute;
          inset: 0;
          background-image: var(--login-bg);
          background-size: cover;
          background-position: center 10%;
          opacity: 0.7;
        }

        .login-portal__overlay {
          position: absolute;
          inset: 0;
          background-image: var(--login-overlay);
          background-size: cover;
          background-position: center;
          opacity: 0.22;
        }

        .login-portal::before {
          content: '';
          position: absolute;
          inset: 0;
          background: linear-gradient(180deg, rgba(8, 12, 32, 0.85) 0%, rgba(18, 24, 52, 0.92) 100%);
          z-index: 1;
        }

        .login-portal__glow {
          position: absolute;
          border-radius: 50%;
          filter: blur(120px);
          opacity: 0.35;
          z-index: 1;
        }

        .login-portal__glow--primary {
          width: 340px;
          height: 340px;
          background: rgba(131, 165, 255, 0.55);
          top: 15%;
          left: 12%;
        }

        .login-portal__glow--secondary {
          width: 320px;
          height: 320px;
          background: rgba(123, 186, 255, 0.45);
          bottom: 10%;
          right: 15%;
        }

        .login-portal__floating {
          position: absolute;
          pointer-events: none;
          z-index: 2;
        }

        .login-portal__floating--one {
          top: clamp(2rem, 8vw, 6rem);
          right: clamp(1rem, 7vw, 6rem);
          width: clamp(160px, 22vw, 220px);
        }

        .login-portal__floating--two {
          bottom: clamp(8rem, 12vw, 10rem);
          left: clamp(0.5rem, 8vw, 6rem);
          width: clamp(130px, 20vw, 200px);
        }

        .login-portal__floating--three {
          bottom: clamp(2rem, 6vw, 5rem);
          right: clamp(1rem, 6vw, 5rem);
          width: clamp(140px, 18vw, 190px);
        }

        .login-portal__content {
          position: relative;
          z-index: 3;
          display: grid;
          gap: clamp(2rem, 6vw, 4rem);
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          padding: 0 clamp(1.5rem, 7vw, 6.5rem);
          margin-top: clamp(2rem, 5vw, 4rem);
        }

        .login-portal__hero {
          max-width: 560px;
          color: rgba(237, 242, 255, 0.9);
        }

        .login-portal__pretitle {
          font-size: 0.85rem;
          text-transform: uppercase;
          letter-spacing: 0.2em;
          color: rgba(183, 209, 255, 0.65);
          margin-bottom: 0.8rem;
        }

        .login-portal__headline {
          font-size: clamp(2.2rem, 5vw, 3.4rem);
          font-weight: 700;
          line-height: 1.05;
          margin-bottom: 1rem;
        }

        .login-portal__headline span {
          color: #8faeff;
          text-shadow: 0 10px 32px rgba(143, 174, 255, 0.25);
        }

        .login-portal__copy {
          font-size: 1.05rem;
          line-height: 1.55;
          color: rgba(217, 228, 255, 0.75);
        }

        .login-portal__card {
          background: rgba(13, 18, 44, 0.82);
          border-radius: 1.6rem;
          border: 1px solid rgba(131, 165, 255, 0.16);
          box-shadow: 0 24px 60px rgba(9, 12, 32, 0.55);
          overflow: hidden;
        }

        .login-portal__card-body {
          padding: clamp(1.5rem, 5vw, 2.5rem);
          display: flex;
          flex-direction: column;
          gap: 1.2rem;
        }

        .login-portal__card-header {
          display: flex;
          align-items: center;
          gap: 1rem;
          color: rgba(237, 242, 255, 0.9);
        }

        .login-portal__card-header h2 {
          font-size: 1.35rem;
          font-weight: 600;
          margin-bottom: 0.25rem;
        }

        .login-portal__card-header p {
          font-size: 0.9rem;
          color: rgba(217, 228, 255, 0.65);
        }

        .login-portal__avatar {
          width: 64px;
          height: 64px;
          border-radius: 18px;
          background: linear-gradient(180deg, rgba(143, 174, 255, 0.35) 0%, rgba(75, 96, 181, 0.48) 100%);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 2rem;
          box-shadow:
            inset 0 1px 8px rgba(255, 255, 255, 0.22),
            0 12px 28px rgba(12, 15, 38, 0.45);
        }

        .login-portal__message {
          border-radius: 1rem;
          padding: 0.8rem 1rem;
          font-size: 0.9rem;
          line-height: 1.4;
        }

        .login-portal__message--error {
          background: rgba(255, 117, 117, 0.14);
          border: 1px solid rgba(255, 117, 117, 0.4);
          color: #ffd5d5;
        }

        .login-portal__message--success {
          background: rgba(102, 226, 166, 0.14);
          border: 1px solid rgba(102, 226, 166, 0.35);
          color: #c9ffe3;
        }

        .login-portal__button {
          width: 100%;
          font-size: 1rem;
          padding: 0.95rem 1rem;
        }

        .login-portal__hint {
          font-size: 0.85rem;
          color: rgba(217, 228, 255, 0.55);
        }

        .login-portal__status {
          display: flex;
          align-items: center;
          gap: 0.55rem;
          font-size: 0.85rem;
          color: rgba(217, 228, 255, 0.6);
        }

        .login-portal__dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: rgba(143, 174, 255, 0.4);
          position: relative;
        }

        .login-portal__dot.is-online {
          background: rgba(126, 237, 181, 0.9);
          box-shadow: 0 0 0 4px rgba(126, 237, 181, 0.25);
        }

        .login-portal__dot.is-idle {
          background: rgba(143, 174, 255, 0.55);
        }

        .login-portal__footer {
          position: relative;
          z-index: 3;
          margin-top: auto;
          padding: 0 clamp(1.5rem, 7vw, 6.5rem) clamp(1.8rem, 4vw, 3rem);
          color: rgba(200, 215, 255, 0.7);
          font-size: 0.9rem;
          line-height: 1.5;
        }

        .login-portal__footer strong {
          color: rgba(239, 245, 255, 0.95);
        }

        .login-portal__loading {
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 1rem;
          background: radial-gradient(circle at top, rgba(143, 174, 255, 0.25), rgba(14, 18, 44, 0.9));
          color: rgba(237, 242, 255, 0.85);
          text-align: center;
        }

        .login-portal__spinner {
          width: 3.5rem;
          height: 3.5rem;
          border-radius: 50%;
          border: 4px solid rgba(239, 245, 255, 0.25);
          border-top-color: #7c9bff;
          animation: spin 1s linear infinite;
        }

        .login-portal__loading-title {
          font-size: 1.1rem;
          font-weight: 600;
        }

        .login-portal__loading-subtitle {
          font-size: 0.9rem;
          color: rgba(217, 228, 255, 0.65);
        }

        @keyframes spin {
          to {
            transform: rotate(360deg);
          }
        }

        @media (max-width: 900px) {
          .login-portal__floating--one,
          .login-portal__floating--two,
          .login-portal__floating--three {
            opacity: 0.35;
          }
        }

        @media (max-width: 720px) {
          .login-portal__content {
            grid-template-columns: 1fr;
            padding: 0 1.25rem;
          }

          .login-portal__footer {
            padding: 0 1.25rem 2rem;
          }

          .login-portal__card-header {
            flex-direction: column;
            text-align: center;
          }

          .login-portal__status {
            justify-content: center;
          }
        }
      `}</style>
    </div>
  );
};

export default LoginPage;