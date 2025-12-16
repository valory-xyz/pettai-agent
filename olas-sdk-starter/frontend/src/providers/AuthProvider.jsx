import React, {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useRef,
	useState,
} from 'react';
import { getOriginAliases } from '../utils/originAliases';

const AuthContext = createContext(null);
const POPUP_FEATURES = 'width=420,height=720,resizable=yes,scrollbars=yes';

export const AuthProvider = ({ children }) => {
	const [ready, setReady] = useState(false);
	const [authenticated, setAuthenticated] = useState(false);
	const [isPopupOpen, setIsPopupOpen] = useState(false);
	const [wsPet, setWsPet] = useState(null);
	const [authFailed, setAuthFailed] = useState(false);
	const [authError, setAuthError] = useState(null);
	const [popupStatus, setPopupStatus] = useState(null);
	const [sessionResetSeq, setSessionResetSeq] = useState(0);
	const popupRef = useRef(null);

	const allowedOrigins = useMemo(() => {
		if (typeof window === 'undefined') {
			return [];
		}
		return getOriginAliases(window.location.origin);
	}, []);

	const clearClientAuthStorage = useCallback(() => {
		if (typeof window === 'undefined') return;
		const clearFromStorage = storage => {
			if (!storage) return;
			try {
				const keysToRemove = [];
				for (let i = 0; i < storage.length; i += 1) {
					const key = storage.key(i);
					if (!key) continue;
					const lower = key.toLowerCase();
					if (
						lower.includes('privy') ||
						lower.includes('pett') ||
						lower.includes('auth')
					) {
						keysToRemove.push(key);
					}
				}
				keysToRemove.forEach(key => storage.removeItem(key));
			} catch (error) {
				console.warn('[Auth] Unable to clear storage during logout', error);
			}
		};

		clearFromStorage(window.localStorage);
		clearFromStorage(window.sessionStorage);
	}, []);

	const cleanupPopup = useCallback(() => {
		if (popupRef.current) {
			try {
				if (!popupRef.current.closed) {
					popupRef.current.close();
				}
			} catch (error) {
				console.warn('[Auth] Unable to close popup window', error);
			}
			popupRef.current = null;
		}
		setIsPopupOpen(false);
	}, []);

	const authenticateWithBackend = useCallback(async token => {
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
				console.error('[Auth] Backend login failed:', data);
				setWsPet(null);
				setAuthFailed(true);
				setAuthenticated(false);
				const backendMessage =
					data?.message || 'Backend login failed. Please try again.';
				setAuthError(backendMessage);
				setPopupStatus({
					status: 'error',
					message: backendMessage,
					error: data,
					timestamp: Date.now(),
				});
				return;
			}

			console.log('[Auth] Backend login successful:', data);

			setWsPet(data.name || 'Connected');
			setAuthFailed(false);
			setAuthError(null);
			setAuthenticated(true);
			setSessionResetSeq(0);
			setPopupStatus({
				status: 'completed',
				message: 'Authenticated successfully. Connecting to your Pett agent…',
				timestamp: Date.now(),
			});
		} catch (error) {
			console.error('[Auth] Error sending Privy token:', error?.message || error);
			setWsPet(null);
			setAuthFailed(true);
			setAuthenticated(false);
			const backendErrorMessage =
				error?.message || 'Unable to authenticate with backend.';
			setAuthError(backendErrorMessage);
			setPopupStatus({
				status: 'error',
				message: backendErrorMessage,
				error,
				timestamp: Date.now(),
			});
		}
	}, []);

	useEffect(() => {
		let isMounted = true;

		const handleMessage = event => {
			if (!allowedOrigins.includes(event.origin)) return;
			const { type, token, status, message, error } = event.data || {};

			if (type === 'privy-token' && token) {
				cleanupPopup();
				setPopupStatus({
					status: 'token-received',
					message: 'Privy token received. Finalizing authentication…',
					timestamp: Date.now(),
				});
				authenticateWithBackend(token);
			}

			if (type === 'privy-popup-status') {
				setPopupStatus({
					status: status || 'unknown',
					message: message || '',
					error: error || null,
					timestamp: Date.now(),
				});
				if (status === 'error') {
					setAuthFailed(true);
					setAuthError(error?.message || message || 'Login failed. Please try again.');
				} else if (!error) {
					setAuthFailed(false);
					setAuthError(null);
				}
			}

			if (type === 'privy-popup-error') {
				const popupMessage =
					error?.message || message || 'Login window reported an error. Please try again.';
				setPopupStatus({
					status: 'error',
					message: popupMessage,
					error: error || null,
					timestamp: Date.now(),
				});
				setAuthFailed(true);
				setAuthError(popupMessage);
				cleanupPopup();
			}

			if (type === 'privy-popup-closed') {
				setPopupStatus({
					status: 'closed',
					message: message || 'Login window closed.',
					timestamp: Date.now(),
				});
				cleanupPopup();
			}
		};

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

				setAuthenticated(true);
				setAuthFailed(false);
				setAuthError(null);
				setSessionResetSeq(0);
				setWsPet(prev => prev || data?.pet?.name || 'Connected');
			} catch (error) {
				console.warn('[Auth] Unable to restore existing session from backend:', error);
			} finally {
				if (isMounted) {
					setReady(true);
				}
			}
		};

		window.addEventListener('message', handleMessage);
		restoreSessionIfAvailable();

		return () => {
			isMounted = false;
			window.removeEventListener('message', handleMessage);
		};
	}, [allowedOrigins, authenticateWithBackend, cleanupPopup]);

	useEffect(() => {
		if (!isPopupOpen) return undefined;
		const checker = setInterval(() => {
			if (popupRef.current && popupRef.current.closed) {
				popupRef.current = null;
				setIsPopupOpen(false);
			}
		}, 500);

		return () => clearInterval(checker);
	}, [isPopupOpen]);

	const login = useCallback(() => {
		// Clean up any existing popup before opening a new one
		cleanupPopup();
		
		const popupUrl = new URL('/privy-login', window.location.origin);
		if (sessionResetSeq > 0) {
			popupUrl.searchParams.set('forceLogout', '1');
			popupUrl.searchParams.set('resetSeq', String(sessionResetSeq));
		}

		// Use a unique window name each time to prevent browser from reusing existing window
		// This ensures it opens as a popup, not a tab, and maintains window.opener reference
		const windowName = `privy-login-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
		
		const popup = window.open(popupUrl.toString(), windowName, POPUP_FEATURES);
		if (popup) {
			// Verify the popup was opened correctly and has opener reference
			// If popup is null or opener is lost, it might have opened as a tab
			try {
				// Small delay to ensure popup is fully initialized
				setTimeout(() => {
					if (popup.closed) {
						console.warn('[Auth] Popup was closed immediately after opening');
						setIsPopupOpen(false);
						return;
					}
				}, 100);
			} catch (error) {
				console.warn('[Auth] Error checking popup state:', error);
			}
			
			popupRef.current = popup;
			setIsPopupOpen(true);
			setPopupStatus({
				status: 'opening',
				message: 'Opening secure Privy login…',
				timestamp: Date.now(),
			});
			popup.focus();
			setAuthError(null);
		} else {
			const message =
				'Unable to open login window. Please allow popups and try again.';
			setPopupStatus({
				status: 'error',
				message,
				timestamp: Date.now(),
			});
			setAuthError(message);
		}
	}, [sessionResetSeq, cleanupPopup]);

	const logout = useCallback(async () => {
		try {
			await fetch('/api/logout', { method: 'POST' });
		} catch (e) {
			console.warn('[Auth] Backend logout failed (continuing):', e);
		}
		clearClientAuthStorage();
		setSessionResetSeq(seq => seq + 1);
		cleanupPopup();
		setWsPet(null);
		setAuthFailed(false);
		setAuthError(null);
		setAuthenticated(false);
		setPopupStatus(null);
	}, [cleanupPopup, clearClientAuthStorage]);

	const value = {
		login,
		logout,
		authenticated,
		ready,
		user: null,
		wsPet,
		authFailed,
		authError,
		isModalOpen: isPopupOpen,
		popupStatus,
	};

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
	const context = useContext(AuthContext);
	if (!context) {
		throw new Error('useAuth must be used within AuthProvider');
	}
	return context;
};
