import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../providers/AuthProvider';
import PetStats from '../components/pet/PetStats';
import XpLevel from '../components/pet/XpLevel';
// ChatHistory disabled for now
import Pet from '../components/pet/Pet';
import backgroundMain from '../assets/images/background-3.jpg';
import backgroundOverlay from '../assets/images/background-0.jpg';
import './Dashboard.scss';
import headerAssetAip from '../assets/images/header-asset-aip.svg';

// Removed fallback sprite usage; we render layered pet state instead

const LAYOUT_CONSTANTS = {
	BOTTOM_UI_PADDING: 24,
	BOTTOM_UI_POSITION_DELAY: 400,
};

const PETT_GAME_APP_URL = 'https://app.pett.ai';

const formatTimestampDisplay = isoString => {
	if (!isoString) return null;
	const date = new Date(isoString);
	if (Number.isNaN(date.getTime())) return null;
	return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

const LAST_PET_MESSAGES_STORAGE_KEY = 'pett:lastPetMessages';

const Dashboard = () => {
	const { authenticated, ready, logout } = useAuth();
	const navigate = useNavigate();
	const handleLogout = useCallback(async () => {
		try {
			await logout();
		} catch (error) {
			console.error('[Dashboard] Logout failed:', error);
		} finally {
			navigate('/login');
		}
	}, [logout, navigate]);

	const handleViewHistory = useCallback(() => {
		navigate('/action-history');
	}, [navigate]);

	const [healthData, setHealthData] = useState(null);
	const [error, setError] = useState(null);
	const [isAnimating, setIsAnimating] = useState(true);
	const [animations, setAnimations] = useState([]);
	const [previousAipBalance, setPreviousAipBalance] = useState(null);
	const animationTimeoutsRef = useRef([]);
	const authTokenPresentRef = useRef(false);
	const consecutiveHealthFailuresRef = useRef(0);
	const healthFailureHandledRef = useRef(false);
	const authStateRef = useRef(authenticated);
	const fastFloatDown = false;
	// chat history disabled

	const bottomUIRef = useRef(null);
	const [bottomUIOffset, setBottomUIOffset] = useState(LAYOUT_CONSTANTS.BOTTOM_UI_PADDING);

	useEffect(() => {
		return () => {
			animationTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
			animationTimeoutsRef.current = [];
		};
	}, []);

	useEffect(() => {
		if (!ready) return;
		if (!authenticated) {
			navigate('/login');
		}
	}, [authenticated, navigate, ready]);

	useEffect(() => {
		authStateRef.current = authenticated;
	}, [authenticated]);

	useEffect(() => {
		const timer = setTimeout(() => setIsAnimating(false), 600);
		return () => clearTimeout(timer);
	}, []);

	useEffect(() => {
		let intervalId;
		let abortController;

		const fetchHealth = async () => {
			if (abortController) {
				abortController.abort();
			}
			abortController = new AbortController();
			try {
				const res = await fetch('/api/health?refresh=1', { signal: abortController.signal });
				if (!res.ok) throw new Error(`Health endpoint returned ${res.status}`);
				const data = await res.json();
				setHealthData(data);
				authTokenPresentRef.current = Boolean(data?.websocket?.auth_token_present);
				consecutiveHealthFailuresRef.current = 0;
				healthFailureHandledRef.current = false;
				setError(null);
			} catch (err) {
				if (err.name === 'AbortError') return;
				console.error('[Dashboard] Failed to fetch health data', err);
				const failures = consecutiveHealthFailuresRef.current + 1;
				consecutiveHealthFailuresRef.current = failures;

				// Show connection warning after 5 failures - do NOT logout or redirect
				// The user is still logged in, it's just a WebSocket/backend connection issue
				if (failures >= 5 && !healthFailureHandledRef.current) {
					healthFailureHandledRef.current = true;
					setError("Connection to Pett.ai servers lost. The agent will keep retrying…");
					console.warn('[Dashboard] Connection issues detected after 5 failures - NOT logging out');
					return;
				}

				if (failures >= 2) {
					setError('Agent connection unstable. Retrying…');
				}
			}
		};

		fetchHealth();
		intervalId = setInterval(fetchHealth, 5000);
		return () => {
			clearInterval(intervalId);
			if (abortController) {
				abortController.abort();
			}
		};
	}, []);

	useEffect(() => {
		const updateBottomUIPosition = () => {
			if (!bottomUIRef.current) return;
			const rect = bottomUIRef.current.getBoundingClientRect();
			setBottomUIOffset(rect.height + LAYOUT_CONSTANTS.BOTTOM_UI_PADDING);
		};

		const timer = setTimeout(updateBottomUIPosition, LAYOUT_CONSTANTS.BOTTOM_UI_POSITION_DELAY);
		window.addEventListener('resize', updateBottomUIPosition);

		return () => {
			clearTimeout(timer);
			window.removeEventListener('resize', updateBottomUIPosition);
		};
	}, []);

	const conversation = useMemo(() => {
		if (!healthData?.recent) return [];
		const items = [];

		// Convert timestamps to seconds (Unix timestamp)
		const toSeconds = ts => {
			if (!ts) return Date.now() / 1000;
			if (typeof ts === 'number') {
				// If it's already in seconds (< 10^11), return as is
				// Otherwise convert milliseconds to seconds
				return ts > 1e11 ? ts / 1000 : ts;
			}
			const t = Date.parse(ts);
			return Number.isNaN(t) ? Date.now() / 1000 : t / 1000;
		};

		// Friendly phrases for recent actions
		const actionToPhrase = entry => {
			const t = String(entry?.type || '').toUpperCase();
			switch (t) {
				case 'SHOWER':
					return 'I just took a bath';
				case 'SLEEP':
					return 'I went to sleep and rested';
				case 'THROWBALL':
					return 'I played with the ball';
				case 'RUB':
					return 'I got some pets and rubs';
				case 'CONSUMABLES_USE':
					return 'I used a consumable to feel better';
				case 'CONSUMABLES_BUY':
					return 'I bought a consumable for later';
				case 'HOTEL_CHECK_IN':
					return 'I checked into the hotel';
				case 'HOTEL_CHECK_OUT':
					return 'I checked out of the hotel';
				case 'HOTEL_BUY':
					return 'I upgraded my hotel tier';
				case 'ACCESSORY_USE':
					return 'I used an accessory';
				case 'ACCESSORY_BUY':
					return 'I bought a new accessory';
				default:
					return t ? `I performed an action: ${t}` : 'I did something';
			}
		};

		if (Array.isArray(healthData.recent.openai_prompts)) {
			healthData.recent.openai_prompts.forEach((prompt, index) => {
				if (!prompt?.prompt) return;
				const role = prompt.kind?.includes('user') ? 'user' : 'pet';
				items.push({
					id: `prompt-${index}`,
					sender: role,
					message: prompt.prompt,
					timestamp: toSeconds(prompt.timestamp),
				});
			});
		}

		// Map recent actions into friendly pet chat messages
		if (Array.isArray(healthData.recent.actions)) {
			healthData.recent.actions.forEach((act, index) => {
				items.push({
					id: `action-${index}`,
					sender: 'pet',
					message: actionToPhrase(act),
					timestamp: toSeconds(act.timestamp),
				});
			});
		}

		if (Array.isArray(healthData.recent.sent_messages)) {
			healthData.recent.sent_messages.forEach((msg, index) => {
				const summary = msg?.type
					? `${msg.type}${msg.success === false ? ' (failed)' : ''}`
					: msg?.success === false
						? 'Message failed'
						: 'Message sent';
				items.push({
					id: `sent-${index}`,
					sender: 'pet',
					message: summary,
					timestamp: toSeconds(msg.timestamp),
				});
			});
		}

		return items.sort((a, b) => a.timestamp - b.timestamp);
	}, [healthData?.recent]);

	const lastPetMessage = [...conversation].reverse().find(msg => msg.sender === 'pet');

	useEffect(() => {
		if (typeof window === 'undefined' || !lastPetMessage?.message) return;

		const rawTimestamp = lastPetMessage.timestamp;
		let executedAtMs = Date.now();
		if (typeof rawTimestamp === 'number') {
			executedAtMs = rawTimestamp > 1e11 ? rawTimestamp : rawTimestamp * 1000;
		} else if (rawTimestamp) {
			const parsed = Date.parse(rawTimestamp);
			executedAtMs = Number.isNaN(parsed) ? executedAtMs : parsed;
		}

		const entry = {
			id: lastPetMessage.id || `pet-${executedAtMs}`,
			message: lastPetMessage.message,
			executedAt: new Date(executedAtMs).toISOString(),
		};

		try {
			const raw = window.localStorage.getItem(LAST_PET_MESSAGES_STORAGE_KEY);
			let existing = [];
			if (raw) {
				const parsed = JSON.parse(raw);
				if (Array.isArray(parsed)) {
					existing = parsed;
				}
			}
			const withoutCurrent = existing.filter(item => item && item.id !== entry.id);
			const updated = [...withoutCurrent, entry].slice(-50);
			window.localStorage.setItem(LAST_PET_MESSAGES_STORAGE_KEY, JSON.stringify(updated));
		} catch (storageError) {
			console.error('[Dashboard] Failed to persist last pet message', storageError);
		}
	}, [lastPetMessage?.id, lastPetMessage?.message, lastPetMessage?.timestamp]);

	const statsSummary = healthData?.pet?.stats ?? {};
	const economyMode = healthData?.economy_mode ?? null;
	const economyWarningMessage =
		economyMode?.active
			? (economyMode?.message?.trim() ||
				'Economy mode is enabled because the agent is low on $AIP and is prioritizing owned items and earning actions.')
			: null;

	const statusSummary = useMemo(() => {
		const rawStatus = String(healthData?.status || 'unknown').replace(/_/g, ' ').trim();
		const normalizedStatus = rawStatus.length ? rawStatus.toUpperCase() : 'UNKNOWN';
		const isHealthy = Boolean(healthData?.is_healthy);
		let tone = 'ok';
		if (error || ['ERROR', 'STOPPED'].includes(normalizedStatus)) {
			tone = 'error';
		} else if (
			!isHealthy ||
			['INITIALIZING', 'RECONNECTING', 'SHUTTING DOWN', 'STARTING'].includes(normalizedStatus)
		) {
			tone = 'warn';
		}

		const toneStyles = {
			ok: {
				pillClass: 'text-green-200 bg-green-400/15 ring-green-400/30',
				dotClass: 'bg-green-400',
			},
			warn: {
				pillClass: 'text-yellow-50 bg-yellow-400/15 ring-yellow-400/20',
				dotClass: 'bg-yellow-300',
			},
			error: {
				pillClass: 'text-red-50 bg-red-500/15 ring-red-500/30',
				dotClass: 'bg-red-400',
			},
		};

		const { pillClass, dotClass } = toneStyles[tone] ?? toneStyles.ok;
		const updatedAt = healthData?.pet_last_updated_at || healthData?.timestamp || null;

		return {
			label: normalizedStatus,
			pillClass,
			dotClass,
			updatedAt,
			formattedUpdatedAt: formatTimestampDisplay(updatedAt),
		};
	}, [healthData?.status, healthData?.is_healthy, healthData?.pet_last_updated_at, healthData?.timestamp, error]);

	// Balance display from pet data (formatted string), with fallbacks
	const { petAipBalanceDisplay, petAipBalanceValue } = useMemo(() => {
		const raw = healthData?.pet?.balance ?? healthData?.pet_balance;
		if (raw === undefined || raw === null) {
			return { petAipBalanceDisplay: '0.0000', petAipBalanceValue: null };
		}

		const numericValue = Number(raw);
		if (!Number.isFinite(numericValue)) {
			return { petAipBalanceDisplay: String(raw), petAipBalanceValue: null };
		}

		return {
			petAipBalanceDisplay: numericValue.toFixed(4),
			petAipBalanceValue: numericValue,
		};
	}, [healthData?.pet?.balance, healthData?.pet_balance]);

	useEffect(() => {
		if (petAipBalanceValue === null) return;

		if (previousAipBalance === null) {
			setPreviousAipBalance(petAipBalanceValue);
			return;
		}

		if (petAipBalanceValue === previousAipBalance) return;

		const change = petAipBalanceValue - previousAipBalance;
		if (change !== 0) {
			const newAnimation = {
				id: `aip-${Date.now()}-${Math.random().toString(16).slice(2)}`,
				change,
			};
			setAnimations(prev => [...prev, newAnimation]);

			const timeoutId = setTimeout(() => {
				setAnimations(prev => prev.filter(animation => animation.id !== newAnimation.id));
				animationTimeoutsRef.current = animationTimeoutsRef.current.filter(id => id !== timeoutId);
			}, fastFloatDown ? 300 : 3500);

			animationTimeoutsRef.current.push(timeoutId);
		}

		setPreviousAipBalance(petAipBalanceValue);
	}, [fastFloatDown, petAipBalanceValue, previousAipBalance]);

	// Normalize pet data for the Pet component: ignore accessories, keep only base-emotion fields
	const petRaw = healthData?.pet ?? null;
	const rawStats = (petRaw && (petRaw.PetStats || petRaw.stats)) || {};
	const petForView = petRaw
		? {
			PetStats: {
				happiness: Number(rawStats.happiness ?? 100),
				health: Number(rawStats.health ?? 100),
				hunger: Number(rawStats.hunger ?? 100),
				hygiene: Number(rawStats.hygiene ?? 100),
				energy: Number(rawStats.energy ?? 100),
			},
			sleeping: Boolean(petRaw?.sleeping),
			dead: Boolean(petRaw?.dead),
		}
		: null;
	const petName = healthData?.pet?.name?.trim() || '';
	const petDisplayName = petName || 'your pet';
	const isPetDead = Boolean(petRaw?.dead);

	const handleRevivePetClick = useCallback(() => {
		if (typeof window === 'undefined') return;
		window.open(PETT_GAME_APP_URL, '_blank', 'noopener,noreferrer');
	}, []);

	const contentPaddingBottom = bottomUIOffset + 80;


	return (
		<div
			className="relative z-50 min-h-screen w-full flex flex-col items-center overflow-x-hidden"
			style={{
				backgroundImage: `url(${backgroundMain})`,
				backgroundRepeat: 'no-repeat',
				backgroundPosition: 'center 10%',
				backgroundSize: 'auto',
				backgroundColor: '#9ab8f6',
				minHeight: '100vh',
			}}
		>

			<div
				className="fixed inset-0"
				style={{
					backgroundImage: `url(${backgroundOverlay})`,
					backgroundSize: 'cover',
					backgroundPosition: 'center',
					zIndex: 1,
				}}
			/>
		<button
			type="button"
			onClick={handleLogout}
			className="fixed top-4 left-4 z-50 text-white hover:text-gray-100 transition-colors bg-red-600/90 hover:bg-red-700 rounded-full p-2 fade-in-delayed shadow-lg"
			style={{ zIndex: 100 }}
			aria-label="Log out"
			title="Log out"
		>
			<span className="text-sm font-bold">Log Out</span>
		</button>

		<button
			type="button"
			onClick={handleViewHistory}
			className="fixed top-4 right-4 z-50 text-white hover:text-gray-100 transition-colors bg-purple-800/90 hover:bg-purple-900 rounded-full p-2 fade-in-delayed shadow-lg"
			style={{ zIndex: 100 }}
			aria-label="View action history"
			title="View action history"
		>
			<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
				<path fill="currentColor" d="M13.5 8H12v5l4.28 2.54l.72-1.21l-3.5-2.08zM13 3a9 9 0 0 0-9 9H1l3.96 4.03L9 12H6a7 7 0 0 1 7-7a7 7 0 0 1 7 7a7 7 0 0 1-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.9 8.9 0 0 0 13 21a9 9 0 0 0 9-9a9 9 0 0 0-9-9" />
			</svg>
		</button>

		<div
			className="flex-1 flex flex-col items-center relative px-4 py-6 w-full"
			style={{
				minHeight: '100vh',
				overflow: 'visible',
				zIndex: 10,
				paddingBottom: `${contentPaddingBottom}px`,
			}}
		>
				<div className="chat-shell flex flex-col items-center gap-4">
					<div className="w-full flex flex-wrap items-center justify-between gap-3">
						<div className="flex flex-col gap-1">
							<span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-base font-bold ring-1 ${statusSummary.pillClass}`}>
								<span className={`inline-block w-1.5 h-1.5 rounded-full animate-pulse ${statusSummary.dotClass}`} />
								{statusSummary.label}
							</span>
							{statusSummary.formattedUpdatedAt && (
								<span className="text-xs uppercase tracking-wide text-white/60">
									Updated {statusSummary.formattedUpdatedAt}
								</span>
							)}
							{error && (
								<span className="inline-flex items-center gap-2 text-xs font-semibold text-red-100 bg-red-500/20 rounded-full px-3 py-1 border border-red-400/30">
									<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24"><path fill="currentColor" d="M11 7h2v6h-2zm0 8h2v2h-2z" /><path fill="currentColor" d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10s10-4.477 10-10S17.523 2 12 2m0 18a8.01 8.01 0 0 1-8-8a8.01 8.01 0 0 1 8-8a8.01 8.01 0 0 1 8 8a8.01 8.01 0 0 1-8 8"></path></svg>
									{error}
								</span>
							)}
							{economyWarningMessage && (
								<span className="inline-flex items-center gap-2 text-xs font-semibold text-yellow-50 bg-yellow-500/20 rounded-full px-3 py-1 border border-yellow-400/30">
									<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2a10 10 0 1 0 10 10A10.011 10.011 0 0 0 12 2m1 15h-2v-2h2zm0-4h-2V7h2z" /></svg>
									{economyWarningMessage}
								</span>
							)}
						</div>

						<div className="header__asset flex items-center gap-2 pl-1 pr-1.5 py-1 border rounded-full bg-white border-semantic-accent-muted relative">
							<img src={headerAssetAip} className="size-6" alt="AIP" />
							<div className="header__asset--amount text-base font-bold text-semantic-accent-bold">
								{petAipBalanceDisplay} $AIP
							</div>

							{/* Floating balance change animations */}
							{animations.map(animation => (
								<div
									key={animation.id}
									className={`absolute top-full left-[70%] transform -translate-x-1/2 pointer-events-none animate-float-down text-base font-black whitespace-nowrap text-global-red-50 ${animation.change > 0 ? 'text-global-green-60' : ''} ${fastFloatDown ? 'animate-float-down-fast' : ''}`}
								>
									{animation.change > 0 ? '+' : ''}
									{animation.change.toFixed(2)} $AIP
								</div>
							))}
						</div>
					</div>

					{/* Pet Status title and stats moved below pet */}
					<div className={`stats-fade-in ${isAnimating ? 'stats-initial' : ''}`} style={{ marginTop: '28px' }}>
						<div className="text-center text-white text-2xl font-bold mb-2">Pet Status</div>
						<PetStats stats={statsSummary} />
						<XpLevel
							level={Number(rawStats.level ?? NaN)}
							xp={Number(rawStats.xp ?? NaN)}
							xpMin={Number(rawStats.xpMin ?? NaN)}
							xpMax={Number(rawStats.xpMax ?? NaN)}
						/>
					</div>

				<div
					className={`relative mb-4 ${isAnimating ? 'pet-scale-initial' : 'pet-scale-final'}`}
					style={{
						minHeight: '280px',
						width: '100%',
						maxWidth: '400px',
						transform: isAnimating ? 'scale(1) translateY(0)' : `scale(1.3) translateY(${bottomUIOffset}px)`,
						transition: 'transform 0.8s cubic-bezier(0.34, 1.56, 0.64, 1)',
						opacity: 1,
					}}
				>
						<div className="flex flex-col items-center justify-center" style={{ height: '230px', width: '230px', margin: '0 auto' }}>
							<Pet name={healthData?.pet?.name} pet={petForView} size="big" />
						</div>
					</div>


					{/* lastPetMessage && (
						<div
							className={`bubble-container bubble-fade-in ${isAnimating ? 'bubble-initial' : ''}`}
							style={{
								left: '50%',
								transform: 'translateX(-50%)',
								top: '280px',
								position: 'absolute',
								zIndex: 10,
								width: 'calc(100% - 40px)',
								maxWidth: '360px',
							}}
						>
							<ChatPreviewMessage message={lastPetMessage} />
						</div>
					) */}
				</div>
			</div>

		<div
			ref={bottomUIRef}
			className={`fixed bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 via-black/40 to-transparent backdrop-blur-sm p-6 slide-up ${isAnimating ? 'slide-up-initial' : ''}`}
			style={{
				zIndex: 50,
					background:
						'linear-gradient(to top, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0.4) 30%, rgba(0,0,0,0.2) 60%, transparent 100%)',
					backdropFilter: 'blur(8px)',
					WebkitBackdropFilter: 'blur(8px)',
				}}
			>
				<div className="chat-shell space-y-4">
					<div className="flex flex-col gap-4">
						{/* <div className="flex gap-2 items-stretch">
							<div className="flex-1 bg-white/90 backdrop-blur-sm rounded-2xl shadow-lg overflow-hidden">
								<textarea
									className="w-full px-5 py-4 text-gray-800 outline-none bg-transparent resize-none"
									placeholder="Type your message..."
									value={inputMessage}
									onChange={e => setInputMessage(e.target.value)}
									disabled={false}
									rows={2}
								/>
								{remainingMessages < 5 && (
									<div className="px-5 pb-2 text-xs text-gray-500">{remainingMessages} messages remaining</div>
								)}
							</div>

							<button
								type="button"
								disabled={!canSendMessage}
								className="bg-purple-600 hover:bg-purple-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white rounded-2xl px-4 py-2 shadow-lg transition-all flex items-center justify-center"
								aria-label="Send message"
								onClick={handleSend}
							>
								<svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
								</svg>
							</button>
						</div> */}
					</div>
				</div>
			</div>

			<style>{`
        .chat-shell {
          width: 100%;
          max-width: 420px;
          margin: 0 auto;
        }
        .background-fade {
          transition: opacity 0.6s ease-out;
        }
        .background-initial {
          opacity: 0;
        }
        .pet-scale-initial {
          transform: scale(1) translateY(0);
          opacity: 1 !important;
        }
        .pet-scale-final {
          transform: scale(1.3);
          opacity: 1 !important;
          transition: transform 0.8s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        .bubble-container {
          display: flex;
          justify-content: center;
        }
        .bubble-fade-in {
          animation: bubble-fade-in 0.6s ease-out 0.5s both;
        }
        .bubble-initial {
          opacity: 0;
          transform: translateY(-20px) scale(0.95);
        }
        .stats-fade-in {
          animation: stats-fade-in 0.5s ease-out 0.2s both;
        }
        .stats-initial {
          opacity: 0;
          transform: translateY(-6px) scale(0.98);
        }
        @keyframes stats-fade-in {
          from {
            opacity: 0;
            transform: translateY(-6px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        @keyframes bubble-fade-in {
          from {
            opacity: 0;
            transform: translateY(-20px) scale(0.95);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        .slide-up {
          transition: transform 0.8s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.8s ease-out;
          mask: linear-gradient(to top, black 0%, black 80%, transparent 100%);
          -webkit-mask: linear-gradient(to top, black 0%, black 80%, transparent 100%);
        }
        .slide-up-initial {
          opacity: 0;
          transform: translateY(100%);
        }
        .fade-in-delayed {
          animation: fade-in-delayed 0.4s ease-out 0.6s both;
        }
        @keyframes fade-in-delayed {
          from {
            opacity: 0;
            transform: scale(0.9);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }
        .animate-history-expand {
          animation: history-expand 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        @keyframes history-expand {
          from {
            opacity: 0;
            transform: translateY(-10px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        @keyframes slide-up-history {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        .animate-slide-up {
          animation: slide-up-history 0.3s ease-out;
        }
      `}</style>
			{isPetDead && (
				<div className="fixed inset-0 z-[120] flex items-center justify-center px-4 py-8">
					<div className="absolute inset-0 bg-black/70 backdrop-blur-[6px]" />
					<div
						role="dialog"
						aria-modal="true"
						className="relative z-10 w-full max-w-md rounded-3xl bg-white/95 p-8 text-center shadow-2xl space-y-6 border border-red-100"
					>
						<div className="space-y-4">
							<div className="flex items-center justify-center">
								<div className="rounded-full bg-red-100 p-4">
									<svg xmlns="http://www.w3.org/2000/svg" className="w-12 h-12 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
										<path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
									</svg>
								</div>
							</div>
							<h2 className="text-3xl font-bold uppercase tracking-wide text-red-600">Important</h2>
							<div className="space-y-3">
								<p className="text-lg font-semibold text-gray-900">
									Unfortunately, your pet{' '}
									{petName ? <span className="font-black text-red-600">{petName}</span> : null}
									{!petName && ' '}
									has passed away!
								</p>
								<p className="text-lg font-medium text-gray-800 leading-relaxed">
									You can still revive or reset {petDisplayName} through the Pett.ai app. Click the button below to open the app, log in with the same method used here, and follow the pet reset/revival instructions.
								</p>
							</div>
						</div>
						<button
							type="button"
							onClick={handleRevivePetClick}
							className="w-full bg-purple-600 hover:bg-purple-700 text-white font-bold py-4 px-6 rounded-xl shadow-lg transition-all transform hover:scale-105 active:scale-95"
						>
							Open Pett.ai App to Revive Pet
						</button>
					</div>
				</div>
			)}
		</div>
	);
};

export default Dashboard;
