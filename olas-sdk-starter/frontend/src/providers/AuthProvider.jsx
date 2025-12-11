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
				setAuthError(
					data?.message || 'Backend login failed. Please try again.'
				);
				return;
			}

			console.log('[Auth] Backend login successful:', data);

			setWsPet(data.name || 'Connected');
			setAuthFailed(false);
			setAuthError(null);
			setAuthenticated(true);
		} catch (error) {
			console.error('[Auth] Error sending Privy token:', error?.message || error);
			setAuthFailed(true);
			setAuthenticated(false);
			setAuthError(error?.message || 'Unable to authenticate with backend.');
		}
	}, []);

	useEffect(() => {
		setReady(true);
		const handleMessage = event => {
			if (event.origin !== window.location.origin) return;
			const { type, token } = event.data || {};
			if (type === 'privy-token' && token) {
				cleanupPopup();
				authenticateWithBackend(token);
			}
			if (type === 'privy-popup-closed') {
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
			popup.focus();
			setAuthError(null);
		} else {
			setAuthError(
				'Unable to open login window. Please allow popups and try again.'
			);
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
