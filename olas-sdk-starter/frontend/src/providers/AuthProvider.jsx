/**
 * AuthProvider - Simple popup-based authentication
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

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
	const [privyToken, setPrivyToken] = useState(null);
	const [wsPet, setWsPet] = useState(null);
	const [isLoggingIn, setIsLoggingIn] = useState(false);
	const [loginError, setLoginError] = useState(null);

	const popupRef = useRef(null);
	const channelRef = useRef(null);
	const handleTokenRef = useRef(null);

	const authenticated = !!privyToken && !!wsPet;

	// Restore session on mount
	useEffect(() => {
		let mounted = true;
		const checkSession = async () => {
			try {
				const res = await fetch('/api/health');
				if (!res.ok) return;
				const data = await res.json();
				const isAuth = data?.websocket?.authenticated || data?.pet?.connected;
				if (isAuth && mounted) {
					setWsPet(data?.pet?.name || 'Connected');
					setPrivyToken('restored'); // Mark as having a token
				}
			} catch (e) {
				// ignore
			}
		};
		checkSession();
		return () => {
			mounted = false;
		};
	}, []);

	// Close popup helper
	const closePopup = useCallback(() => {
		if (popupRef.current && !popupRef.current.closed) {
			try {
				popupRef.current.close();
			} catch (e) {
				// ignore
			}
		}
		popupRef.current = null;
	}, []);

	// Handle token from popup
	const handleToken = useCallback(
		async (token) => {
			console.log('[AuthProvider] Received token from popup:', token?.substring(0, 20) + '...');
			if (!token) return;

			try {
				const res = await fetch('/api/login', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ privy_token: token }),
				});
				const data = await res.json();
				console.log('[AuthProvider] Backend response:', data);

				if (res.ok && data?.success) {
					console.log('[AuthProvider] Login successful, setting state...');
					setPrivyToken(token);
					setWsPet(data.name || 'Connected');
					setLoginError(null);
					console.log('[AuthProvider] State updated, authenticated should be true now');
				} else if (data?.pet_connected || data?.pet?.connected) {
					console.log('[AuthProvider] Pet already connected');
					setPrivyToken(token);
					setWsPet(data?.pet_name || data?.pet?.name || 'Connected');
					setLoginError(null);
				} else {
					console.error('[AuthProvider] Login failed:', data?.message);
					setLoginError(data?.message || 'Login failed');
				}
			} catch (err) {
				console.error('[AuthProvider] Login error:', err);
				setLoginError(err?.message || 'Login failed');
			} finally {
				setIsLoggingIn(false);
				closePopup();
			}
		},
		[closePopup]
	);

	// Keep handleToken ref in sync
	useEffect(() => {
		handleTokenRef.current = handleToken;
	}, [handleToken]);

	// Setup BroadcastChannel and postMessage listeners
	useEffect(() => {
		console.log('[AuthProvider] Setting up message listeners');

		// Setup BroadcastChannel listener (persistent)
		if (!channelRef.current) {
			channelRef.current = new BroadcastChannel('pett-auth');

			channelRef.current.onmessage = (event) => {
				console.log('[AuthProvider] BroadcastChannel message received:', event.data);
				const { type, token } = event.data || {};
				if (type === 'token' && token && handleTokenRef.current) {
					console.log('[AuthProvider] Calling handleToken with token from BroadcastChannel');
					handleTokenRef.current(token);
				} else if (type === 'closed') {
					console.log('[AuthProvider] Popup closed message received');
					setIsLoggingIn(false);
				}
			};
		}

		// Also listen for postMessage as backup
		const handlePostMessage = (event) => {
			// Only accept messages from same origin
			if (event.origin !== window.location.origin) return;

			console.log('[AuthProvider] postMessage received:', event.data);
			const { type, token } = event.data || {};
			if (type === 'token' && token && handleTokenRef.current) {
				console.log('[AuthProvider] Calling handleToken with token from postMessage');
				handleTokenRef.current(token);
			}
		};

		window.addEventListener('message', handlePostMessage);

		return () => {
			window.removeEventListener('message', handlePostMessage);
			// Don't close BroadcastChannel on unmount - keep it open for the app lifecycle
			console.log('[AuthProvider] Cleanup - keeping BroadcastChannel open');
		};
	}, []);

	const login = useCallback(() => {
		console.log('[AuthProvider] Opening login popup');
		// Reset state
		setLoginError(null);
		setIsLoggingIn(true);
		closePopup();

		// Open popup
		const popup = window.open(
			'/privy-login',
			`privy-login-${Date.now()}`,
			'width=420,height=720,resizable=yes,scrollbars=yes'
		);

		if (!popup) {
			setLoginError('Popup blocked. Please allow popups.');
			setIsLoggingIn(false);
			return;
		}

		popupRef.current = popup;

		// Check if popup was closed
		const checkClosed = setInterval(() => {
			if (!popupRef.current || popupRef.current.closed) {
				clearInterval(checkClosed);
				setIsLoggingIn(false);
				popupRef.current = null;
			}
		}, 500);
	}, [closePopup]);

	const logout = useCallback(async () => {
		console.log('[AuthProvider] Logging out');
		// Clear state first
		setPrivyToken(null);
		setWsPet(null);
		setLoginError(null);
		setIsLoggingIn(false);
		closePopup();

		// Call backend logout
		try {
			await fetch('/api/logout', { method: 'POST' });
		} catch (e) {
			// ignore
		}
	}, [closePopup]);

	const value = useMemo(
		() => ({
			authenticated,
			wsPet,
			isModalOpen: isLoggingIn,
			authError: loginError,
			login,
			logout,
			ready: true,
		}),
		[authenticated, wsPet, isLoggingIn, loginError, login, logout]
	);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
	const ctx = useContext(AuthContext);
	if (!ctx) throw new Error('useAuth must be used within AuthProvider');
	return ctx;
};

export default AuthProvider;
