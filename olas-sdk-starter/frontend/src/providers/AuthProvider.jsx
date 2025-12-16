/**
 * AuthProvider - Example implementation that works in Electron iframe contexts
 * 
 * This provider handles authentication using Privy in a way that works when:
 * 1. Running in a standard browser
 * 2. Running inside an Electron application
 * 3. Running inside an iframe within Electron
 * 
 * Key Changes for Electron Iframe Support:
 * - Uses BroadcastChannel as primary communication method
 * - Falls back to localStorage events for same-origin messaging
 * - Handles window.opener being null after logout
 * - Properly tracks popup state even when reference is lost
 */

import React, {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
	useRef,
} from 'react';
import { usePrivy } from '@privy-io/react-auth';
import { useAuthMessageListener } from '../hooks/useAuthMessageListener';
import { getOriginAliases } from '../utils/originAliases';

// Create the context
const AuthContext = createContext(null);

// Detect environment
const isElectron = () => {
	if (typeof window === 'undefined') return false;
	return !!(
		window.process?.versions?.electron ||
		window.navigator?.userAgent?.includes('Electron') ||
		window.electronAPI
	);
};

const isInIframe = () => {
	if (typeof window === 'undefined') return false;
	try {
		return window.self !== window.top;
	} catch (e) {
		return true;
	}
};

export const AuthProvider = ({ children }) => {
	const { ready, authenticated: privyAuthenticated, logout: privyLogout } = usePrivy();

	// Local auth state
	const [privyToken, setPrivyToken] = useState(null);
	const [wsPet, setWsPet] = useState(null);
	const [isLoggingIn, setIsLoggingIn] = useState(false);
	const [loginError, setLoginError] = useState(null);
	const [popupStatus, setPopupStatus] = useState(null);

	// Popup reference
	const popupRef = useRef(null);

	// Derived state
	const authenticated = !!privyToken && !!wsPet;

	// Authenticate with backend using Privy token
	const authenticateWithBackend = useCallback(async (token) => {
		if (!token) {
			return;
		}
		try {
			const response = await fetch('/api/login', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ privy_token: token }),
			});
			const data = await response.json();

			if (!response.ok || data?.success !== true) {
				console.error('[AuthProvider] Backend login failed:', data);
				setWsPet(null);
				setLoginError(data?.message || 'Backend login failed. Please try again.');
				setIsLoggingIn(false);
				setPopupStatus({
					status: 'error',
					message: data?.message || 'Backend login failed. Please try again.',
					error: data,
					timestamp: Date.now(),
				});
				return;
			}

			console.log('[AuthProvider] Backend login successful:', data);

			setWsPet(data.name || 'Connected');
			setPrivyToken(token);
			setLoginError(null);
			setIsLoggingIn(false);
			setPopupStatus({
				status: 'completed',
				message: 'Authenticated successfully. Connecting to your Pett agent…',
				timestamp: Date.now(),
			});

			// Close popup if we have a reference
			try {
				if (popupRef.current && !popupRef.current.closed) {
					popupRef.current.close();
				}
			} catch (e) {
				// Popup may already be closed
			}
			popupRef.current = null;
		} catch (error) {
			console.error('[AuthProvider] Error sending Privy token:', error?.message || error);
			setWsPet(null);
			setLoginError(error?.message || 'Unable to authenticate with backend.');
			setIsLoggingIn(false);
			setPopupStatus({
				status: 'error',
				message: error?.message || 'Unable to authenticate with backend.',
				error,
				timestamp: Date.now(),
			});
		}
	}, []);

	// Use the useAuthMessageListener hook to handle all message channels
	useAuthMessageListener({
		onToken: (token) => {
			console.log('[AuthProvider] Token received via useAuthMessageListener');
			authenticateWithBackend(token);
		},
		onError: (error) => {
			console.error('[AuthProvider] Popup error:', error);
			setLoginError(error?.message || 'Login failed. Please try again.');
			setIsLoggingIn(false);
			setPopupStatus({
				status: 'error',
				message: error?.message || 'Login failed. Please try again.',
				error,
				timestamp: Date.now(),
			});
		},
		onPopupClosed: () => {
			console.log('[AuthProvider] Popup closed');
			setIsLoggingIn(false);
			popupRef.current = null;
		},
		onStatusChange: (status, message) => {
			console.log('[AuthProvider] Popup status:', status, message);
			setPopupStatus({
				status,
				message: message || '',
				timestamp: Date.now(),
			});
		},
	});

	// Restore session if available on mount
	useEffect(() => {
		let isMounted = true;

		const restoreSessionIfAvailable = async () => {
			try {
				const response = await fetch('/api/health');
				if (!response.ok) {
					throw new Error(`Health check failed with status ${response.status}`);
				}
				const data = await response.json();
				const isAuthenticated =
					Boolean(data?.websocket?.authenticated) ||
					Boolean(data?.pet?.connected) ||
					Boolean(data?.websocket?.auth_token_present);

				if (!isAuthenticated || !isMounted) return;

				setWsPet(data?.pet?.name || 'Connected');
				// Note: We don't set privyToken here since we don't have it from the health check
				// The authenticated state will be false until we get a token
			} catch (error) {
				console.warn('[AuthProvider] Unable to restore existing session from backend:', error);
			} finally {
				if (isMounted) {
					// ready state is managed by Privy
				}
			}
		};

		if (ready) {
			restoreSessionIfAvailable();
		}

		return () => {
			isMounted = false;
		};
	}, [ready]);

	// Get allowed origins for postMessage validation
	const allowedOrigins = useMemo(() => {
		if (typeof window === 'undefined') {
			return [];
		}
		return getOriginAliases(window.location.origin);
	}, []);

	// Cleanup popup
	const cleanupPopup = useCallback(() => {
		if (popupRef.current) {
			try {
				if (!popupRef.current.closed) {
					popupRef.current.close();
				}
			} catch (error) {
				console.warn('[AuthProvider] Unable to close popup window', error);
			}
			popupRef.current = null;
		}
		setIsLoggingIn(false);
	}, []);

	// Open the login popup
	const openLoginPopup = useCallback((forceLogout = false) => {
		// Clean up any existing popup before opening a new one
		cleanupPopup();

		const popupUrl = new URL('/privy-login', window.location.origin);
		if (forceLogout) {
			popupUrl.searchParams.set('forceLogout', '1');
		}

		// Use a unique window name each time to prevent browser from reusing existing window
		// This ensures it opens as a popup, not a tab, and maintains window.opener reference
		const windowName = `privy-login-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;

		// Popup features that force a popup window (not a tab)
		const POPUP_FEATURES = 'width=420,height=720,resizable=yes,scrollbars=yes,menubar=no,toolbar=no,location=no,status=no';

		setIsLoggingIn(true);
		setLoginError(null);

		// window.open must be called synchronously in response to user action
		const popup = window.open(popupUrl.toString(), windowName, POPUP_FEATURES);

		if (popup) {
			popupRef.current = popup;
			setPopupStatus({
				status: 'opening',
				message: 'Opening secure Privy login…',
				timestamp: Date.now(),
			});
			popup.focus();

			// Monitor popup closure
			const checkClosed = setInterval(() => {
				try {
					if (!popupRef.current || popupRef.current.closed) {
						clearInterval(checkClosed);
						setIsLoggingIn(false);
						popupRef.current = null;
					}
				} catch (e) {
					// Access error can happen for cross-origin popups
				}
			}, 500);
		} else {
			const message = 'Unable to open login window. Please allow popups and try again.';
			setPopupStatus({
				status: 'error',
				message,
				timestamp: Date.now(),
			});
			setLoginError(message);
			setIsLoggingIn(false);
		}
	}, [cleanupPopup]);

	// Login function (opens popup)
	const login = useCallback(() => {
		openLoginPopup(false);
	}, [openLoginPopup]);

	// Logout function
	const logout = useCallback(async () => {
		console.log('[AuthProvider] Logging out');

		try {
			// Call backend logout
			await fetch('/api/logout', { method: 'POST' });
		} catch (e) {
			console.warn('[AuthProvider] Backend logout failed (continuing):', e);
		}

		// Clear local state
		setPrivyToken(null);
		setWsPet(null);
		setPopupStatus(null);
		setLoginError(null);
		cleanupPopup();

		// Logout from Privy
		try {
			await privyLogout();
		} catch (error) {
			console.warn('[AuthProvider] Privy logout error:', error);
		}

		console.log('[AuthProvider] Logout successful');
	}, [privyLogout, cleanupPopup]);

	// Login after logout (forces a fresh login)
	const loginAfterLogout = useCallback(async () => {
		console.log('[AuthProvider] Login after logout');

		// First ensure we're logged out
		await logout();

		// Then open popup with forceLogout flag
		// Small delay to ensure logout completes
		setTimeout(() => {
			openLoginPopup(true);
		}, 100);
	}, [logout, openLoginPopup]);

	// Context value
	const value = useMemo(() => ({
		// State
		ready,
		authenticated,
		user: null, // For compatibility
		wsPet,
		isModalOpen: isLoggingIn, // Alias for isLoggingIn
		authError: loginError, // Alias for loginError
		authFailed: !!loginError,
		popupStatus,

		// Environment info
		isElectron: isElectron(),
		isIframe: isInIframe(),

		// Actions
		login,
		logout,
		loginAfterLogout,
	}), [
		ready,
		authenticated,
		wsPet,
		isLoggingIn,
		loginError,
		popupStatus,
		login,
		logout,
		loginAfterLogout,
	]);

	return (
		<AuthContext.Provider value={value}>
			{children}
		</AuthContext.Provider>
	);
};

// Hook to use auth context
export const useAuth = () => {
	const context = useContext(AuthContext);
	if (!context) {
		throw new Error('useAuth must be used within an AuthProvider');
	}
	return context;
};

export default AuthProvider;