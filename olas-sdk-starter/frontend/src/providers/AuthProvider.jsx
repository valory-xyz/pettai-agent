import React, {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useRef,
	useState,
} from 'react';

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
	const popupRef = useRef(null);

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
			setPopupStatus({
				status: 'completed',
				message: 'Authenticated successfully. Connecting to your Pett agent…',
				timestamp: Date.now(),
			});
		} catch (error) {
			console.error('[Auth] Error sending Privy token:', error?.message || error);
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
		setReady(true);
		const handleMessage = event => {
			if (event.origin !== window.location.origin) return;
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

		window.addEventListener('message', handleMessage);
		return () => window.removeEventListener('message', handleMessage);
	}, [authenticateWithBackend, cleanupPopup]);

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
		const popupUrl = new URL('/privy-login', window.location.origin).toString();
		const popup = window.open(popupUrl, 'privy-login', POPUP_FEATURES);
		if (popup) {
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
	}, []);

	const logout = useCallback(async () => {
		try {
			await fetch('/api/logout', { method: 'POST' });
		} catch (e) {
			console.warn('[Auth] Backend logout failed (continuing):', e);
		}
		setWsPet(null);
		setAuthFailed(false);
		setAuthError(null);
		setAuthenticated(false);
		setPopupStatus(null);
	}, []);

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
