import React, { useEffect } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  useNavigate,
  useLocation,
} from 'react-router-dom';
import { PrivyProvider } from '@privy-io/react-auth';
import { AuthProvider, useAuth } from './providers/AuthProvider';
import LoginPage from './pages/LoginPage';
import Dashboard from './pages/Dashboard';
import AllSet from './pages/AllSet';
import ActionHistory from './pages/ActionHistory';
import PrivyLoginPopup from './pages/PrivyLoginPopup';
import './assets/styles/core.scss';
import './assets/styles/toast.scss';
import './assets/styles/tutorial.scss';
import './assets/styles/modals.scss';
import './assets/styles/button.scss';
import './assets/fonts/retro-pixel.css';
import './assets/fonts/satoshi.css';
import './App.css';

// Router component with navigation logic
const RouterWithAuth = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { authenticated, ready, wsPet } = useAuth();

  // Navigate to All Set screen on successful authentication (only from login page)
  useEffect(() => {
    if (
      authenticated &&
      ready &&
      wsPet &&
      (location.pathname === '/login' || location.pathname === '/')
    ) {
      console.log('[App] Authentication successful, navigating to all-set');
      navigate('/all-set', { replace: true });
    }
  }, [authenticated, ready, wsPet, navigate, location.pathname]);

  // Redirect to login if authentication is lost
  useEffect(() => {
    if (
      ready &&
      !authenticated &&
      location.pathname !== '/login' &&
      location.pathname !== '/'
    ) {
      console.warn('[App] Authentication lost, redirecting to login');
      navigate('/login', { replace: true });
    }
  }, [ready, authenticated, navigate, location.pathname]);

  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/action-history" element={<ActionHistory />} />
      <Route path="/all-set" element={<AllSet />} />
    </Routes>
  );
};

const ConfigurationError = () => (
  <div className="App">
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        flexDirection: 'column',
        gap: '1rem',
        padding: '2rem',
        textAlign: 'center',
      }}
    >
      <h1 style={{ color: '#ef4444', fontSize: '1.5rem', fontWeight: 'bold' }}>
        Configuration Error
      </h1>
      <p style={{ color: '#6b7280' }}>
        REACT_APP_PRIVY_APP_ID environment variable is not set. Please configure
        it in your environment.
      </p>
    </div>
  </div>
);

const MainAppShell = () => (
  <div className="App">
    <div className="App-content">
      <AuthProvider>
        <RouterWithAuth />
      </AuthProvider>
    </div>
  </div>
);

const PrivyLoginRoute = ({ appId }) => (
  <PrivyProvider
    appId={appId}
    config={{
      loginMethods: ['email'],
      appearance: {
        theme: 'light',
        accentColor: '#4A90E2',
      },
    }}
  >
    <PrivyLoginPopup />
  </PrivyProvider>
);

// Main App component
function App() {
  const privyAppId = process.env.REACT_APP_PRIVY_APP_ID;

  if (!privyAppId) {
    console.error(
      '[App] REACT_APP_PRIVY_APP_ID environment variable is required'
    );
    return <ConfigurationError />;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/privy-login"
          element={<PrivyLoginRoute appId={privyAppId} />}
        />
        <Route path="/*" element={<MainAppShell />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
