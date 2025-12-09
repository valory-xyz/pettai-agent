import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { useNavigate } from 'react-router-dom';

import backgroundMain from '../assets/images/background-3.jpg';
import backgroundOverlay from '../assets/images/background-0.jpg';

// Local lightweight Icon set copied from PetStat for consistent visuals
const Icon = {
	Food: ({ className, width = 20, height = 18, ...props }) => (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			width={width}
			height={height}
			viewBox="0 0 20 18"
			fill="none"
			{...props}
		>
			<path fillRule="evenodd" clipRule="evenodd" d="M16.21 1.287C16.531 1.73642 16.4269 2.36097 15.9775 2.68198L12.9489 4.84525C12.4461 5.20442 12.3861 5.92914 12.8231 6.36609L12.9214 6.46442L16.4569 2.92888C16.8474 2.53836 17.4806 2.53836 17.8711 2.92888C18.2617 3.31941 18.2617 3.95257 17.8711 4.3431L14.3356 7.87863L14.434 7.97699C14.8709 8.41394 15.5956 8.35396 15.9548 7.85112L18.1181 4.82254C18.4391 4.37312 19.0636 4.26903 19.5131 4.59004C19.9625 4.91105 20.0666 5.5356 19.7455 5.98501L17.5823 9.0136C16.5048 10.5221 14.3306 10.7021 13.0197 9.39121L12.9214 9.29284L11.2749 10.9393L16.0783 15.7428C16.4689 16.1333 16.4689 16.7665 16.0783 17.157C15.6878 17.5475 15.0546 17.5475 14.6641 17.157L9.86068 12.3535L5.14321 17.071C4.75269 17.4615 4.11952 17.4615 3.729 17.071C3.33847 16.6805 3.33847 16.0473 3.729 15.6568L8.44647 10.9393L6.88596 9.37883L5.47177 10.7931C5.28424 10.9806 5.02988 11.086 4.76466 11.086C4.49944 11.086 4.24508 10.9806 4.05755 10.7931L1.85352 8.58906C0.331719 7.06726 -0.0455539 4.7424 0.916918 2.81746L1.0418 2.5677C1.18555 2.28019 1.45875 2.07932 1.77604 2.02783C2.09333 1.97634 2.41603 2.08051 2.64333 2.3078L7.59305 7.25749L9.86068 9.52512L11.5072 7.87863L11.4088 7.78031C10.098 6.46946 10.2779 4.2953 11.7865 3.21779L14.815 1.05451C15.2645 0.733499 15.889 0.837591 16.21 1.287Z" fill="currentColor" />
		</svg>
	),
	Health: ({ className, width = 19, height = 18, ...props }) => (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			width={width}
			height={height}
			viewBox="0 0 19 18"
			fill="none"
			{...props}
		>
			<path d="M5.40015 2C5.40015 0.89543 6.29558 0 7.40015 0H11.4001C12.5047 0 13.4001 0.89543 13.4001 2V5H16.4001C17.5047 5 18.4001 5.89543 18.4001 7V11C18.4001 12.1046 17.5047 13 16.4001 13H13.4001V16C13.4001 17.1046 12.5047 18 11.4001 18H7.40015C6.29558 18 5.40015 17.1046 5.40015 16V13H2.40015C1.29558 13 0.400146 12.1046 0.400146 11V7C0.400146 5.89543 1.29558 5 2.40015 5H5.40015V2Z" fill="currentColor" />
		</svg>
	),
	Power: ({ className, width = 18, height = 22, ...props }) => (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			width={width}
			height={height}
			viewBox="0 0 18 22"
			fill="none"
			{...props}
		>
			<path fillRule="evenodd" clipRule="evenodd" d="M10.7933 0.565462C11.6239 0.959839 12.0383 1.75496 12.1117 2.64448C12.2214 3.97434 11.9552 5.38624 11.7259 6.6982C11.4988 7.99715 11.5221 7.99978 12.7949 7.99966C13.6765 7.99959 14.4514 7.99952 15.0573 8.09271C15.8872 8.22036 16.6324 8.62429 17.0119 9.394C17.6794 10.7475 16.6815 12.2014 16.0354 13.353C14.8354 15.4916 13.4209 17.4114 11.7416 19.1696C10.9733 19.974 10.2897 20.6972 9.66168 21.131C8.92419 21.6405 8.04215 21.8306 7.20654 21.4339C6.37597 21.0395 5.96155 20.2444 5.88816 19.3549C5.77845 18.025 6.04461 16.6131 6.27398 15.3011C6.49532 14.0351 6.52676 13.9996 5.20495 13.9997C4.32338 13.9997 3.5484 13.9998 2.94252 13.9066C2.11264 13.779 1.36748 13.375 0.987926 12.6053C0.320477 11.2518 1.31835 9.79791 1.96449 8.64636C3.16449 6.50774 4.57894 4.58792 6.2582 2.82974C7.02656 2.02529 7.71017 1.30213 8.33816 0.868308C9.07566 0.358841 9.9577 0.168693 10.7933 0.565462Z" fill="currentColor" />
		</svg>
	),
	Gamepad: ({ className, width = 23, height = 16, ...props }) => (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			width={width}
			height={height}
			viewBox="0 0 23 16"
			fill="none"
			{...props}
		>
			<path fillRule="evenodd" clipRule="evenodd" d="M8.50655 0.389901C7.88441 0.197774 7.24399 0 6.60012 0C5.10396 0 4.07745 1.54306 3.43057 2.69922C2.6433 4.10626 1.95857 5.94323 1.50502 7.75747C1.05458 9.5593 0.806731 11.4395 0.965248 12.919C1.04387 13.6529 1.23256 14.3913 1.63795 14.9713C2.07933 15.6028 2.75108 16 3.60012 16C5.42123 16 6.78476 14.8434 7.85488 13.9357C7.87808 13.9161 7.90133 13.8963 7.92462 13.8766C8.98434 12.9771 10.1355 12 11.6001 12C13.0647 12 14.2159 12.9771 15.2756 13.8766C15.2989 13.8963 15.3221 13.9161 15.3453 13.9357C16.4154 14.8434 17.7789 16 19.6001 16C20.4492 16 21.1209 15.6028 21.5623 14.9713C21.9677 14.3913 22.1564 13.6529 22.235 12.919C22.3936 11.4395 22.1457 9.55929 21.6952 7.75746C21.2417 5.94322 20.5569 4.10625 19.7697 2.69921C19.1228 1.54307 18.0963 0 16.6001 0C15.9562 0 15.3158 0.19778 14.6936 0.389912C14.5278 0.441118 14.3633 0.491923 14.2003 0.538476C13.3348 0.785763 12.4501 1 11.6001 1C10.7501 1 9.86538 0.785762 8.99985 0.538475C8.83691 0.491919 8.67238 0.441111 8.50655 0.389901ZM8.1001 6C7.82396 6 7.6001 6.22386 7.6001 6.5C7.6001 6.77614 7.82396 7 8.1001 7C8.37624 7 8.6001 6.77614 8.6001 6.5C8.6001 6.22386 8.37624 6 8.1001 6ZM5.6001 6.5C5.6001 5.11929 6.71939 4 8.1001 4C9.48081 4 10.6001 5.11929 10.6001 6.5C10.6001 7.88071 9.48081 9 8.1001 9C6.71939 9 5.6001 7.88071 5.6001 6.5ZM15.1001 4C14.5478 4 14.1001 4.44772 14.1001 5V5.5H13.6001C13.0478 5.5 12.6001 5.94772 12.6001 6.5C12.6001 7.05228 13.0478 7.5 13.6001 7.5H14.1001V8C14.1001 8.55228 14.5478 9 15.1001 9C15.6524 9 16.1001 8.55228 16.1001 8V7.5H16.6001C17.1524 7.5 17.6001 7.05228 17.6001 6.5C17.6001 5.94772 17.1524 5.5 16.6001 5.5H16.1001V5C16.1001 4.44772 15.6524 4 15.1001 4Z" fill="currentColor" />
		</svg>
	),
	Bath: ({ className, width = 20, height = 18, ...props }) => (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			width={width}
			height={height}
			viewBox="0 0 20 18"
			fill="none"
			{...props}
		>
			<path fillRule="evenodd" clipRule="evenodd" d="M4.99976 3C4.99976 2.44772 5.44747 2 5.99976 2H6.99976V3C6.99976 3.55228 7.44747 4 7.99976 4C8.55204 4 8.99976 3.55228 8.99976 3V2C8.99976 0.895431 8.10433 0 6.99976 0H5.99976C4.3429 0 2.99976 1.34315 2.99976 3V8H1.99976C0.895186 8 -0.000244141 8.89543 -0.000244141 10V11C-0.000244141 13.0621 1.03999 14.8812 2.62434 15.9612L2.29265 16.2929C1.90212 16.6834 1.90212 17.3166 2.29265 17.7071C2.68317 18.0976 3.31634 18.0976 3.70686 17.7071L4.58236 16.8316C5.03681 16.9417 5.51147 17 5.99976 17H13.9998C14.488 17 14.9627 16.9417 15.4171 16.8316L16.2926 17.7071C16.6832 18.0976 17.3163 18.0976 17.7069 17.7071C18.0974 17.3166 18.0974 16.6834 17.7069 16.2929L17.3752 15.9612C18.9595 14.8812 19.9998 13.0621 19.9998 11V10C19.9998 8.89543 19.1043 8 17.9998 8H4.99976V3Z" fill="currentColor" />
		</svg>
	),
};

const ACTION_DETAILS = {
	THROW_BALL: {
		title: 'Tossed the ball',
		description: 'Played fetch; Happiness went up after tossing the ball.',
		stat: 'Happiness',
		statKey: 'happiness',
	},
	THROWBALL: {
		title: 'Played fetch',
		description: 'Had some fun; Happiness went up after tossing the ball.',
		stat: 'Happiness',
		statKey: 'happiness',
	},
	RUB: {
		title: 'Received pets',
		description: 'Soaked up affection; Happiness feels cozy now.',
		stat: 'Happiness',
		statKey: 'happiness',
	},
	SLEEP: {
		title: 'Slept soundly',
		description: 'Slept to recharge Energy for the next adventure.',
		stat: 'Energy',
		statKey: 'energy',
	},
	SHOWER: {
		title: 'Fresh and clean',
		description: 'Took a shower; Hygiene feels sparkling again.',
		stat: 'Hygiene',
		statKey: 'hygiene',
	},
	CONSUMABLES_USE: {
		title: 'Used a consumable',
		description: 'Improved Health by consuming a handy potion.',
		stat: 'Health',
		statKey: 'health',
	},
	CONSUMABLES_BUY: {
		title: 'Restocked supplies',
		description: 'Picked up items to keep Hunger and Health in check.',
		stat: 'Hunger & Health',
		statKey: 'hunger',
	},
	ACCESSORY_USE: {
		title: 'Styled up',
		description: 'Used an accessory; Happiness sparkled with style.',
		stat: 'Happiness',
		statKey: 'happiness',
	},
	ACCESSORY_BUY: {
		title: 'New accessory',
		description: 'Bought something shiny; Happiness is excited to wear it.',
		stat: 'Happiness',
		statKey: 'happiness',
	},
	HOTEL_CHECK_IN: {
		title: 'Hotel check-in',
		description: 'Checked into the hotel to protect Energy overnight.',
		stat: 'Energy',
		statKey: 'energy',
	},
	HOTEL_CHECK_OUT: {
		title: 'Hotel check-out',
		description: 'Checked out feeling refreshed and ready.',
		stat: 'Energy',
		statKey: 'energy',
	},
	HOTEL_BUY: {
		title: 'Hotel upgrade',
		description: 'Upgraded comfort levels; boosts future Energy recovery.',
		stat: 'Energy',
		statKey: 'energy',
	},
};

const STAT_STYLES = {
	hunger: {
		accent: 'bg-global-brand-60',
		chip: 'bg-global-brand-60 text-white border-transparent',
	},
	health: {
		accent: 'bg-global-red-60',
		chip: 'bg-global-red-60 text-white border-transparent',
	},
	energy: {
		accent: 'bg-global-yellow-60',
		chip: 'bg-global-yellow-60 text-gray-900 border-transparent',
	},
	happiness: {
		accent: 'bg-global-green-60',
		chip: 'bg-global-green-60 text-white border-transparent',
	},
	hygiene: {
		accent: 'bg-global-blue-60',
		chip: 'bg-global-blue-60 text-white border-transparent',
	},
};

const STAT_ICON = {
	hunger: Icon.Food,
	health: Icon.Health,
	energy: Icon.Power,
	happiness: Icon.Gamepad,
	hygiene: Icon.Bath,
};

const renderStatIcon = statKey => {
	const StatIcon = STAT_ICON[statKey];
	if (!StatIcon) {
		return null;
	}
	return <StatIcon width={12} height={12} className="w-3 h-3" />;
};

const SCROLLBAR_STYLES = `
.action-history-scroll {
	scrollbar-width: thin;
	scrollbar-color: rgba(255, 255, 255, 0.25) transparent;
	padding-right: 12px;
	margin-right: -12px;
}

.action-history-scroll::-webkit-scrollbar {
	width: 6px;
}

.action-history-scroll::-webkit-scrollbar-track {
	background: transparent;
}

.action-history-scroll::-webkit-scrollbar-thumb {
	background-color: rgba(255, 255, 255, 0.25);
	border-radius: 9999px;
}

.action-history-scroll::-webkit-scrollbar-thumb:hover {
	background-color: rgba(255, 255, 255, 0.35);
}
`;

const formatActionName = name =>
	name
		.replace(/_/g, ' ')
		.toLowerCase()
		.replace(/(^|\s)([a-z])/g, (_, prefix, char) => `${prefix}${char.toUpperCase()}`);

const formatTime = timestamp => {
	if (!timestamp) return '--:--';
	const date = new Date(timestamp);
	if (Number.isNaN(date.getTime())) return '--:--';
	return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const ActionHistory = () => {
	const navigate = useNavigate();
	const [history, setHistory] = useState({ actions: [], epoch: null, completed: 0, required_actions: 0 });
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState(null);
	const [isInfoOpen, setIsInfoOpen] = useState(false);
	const bubbleHoverRef = useRef(false);
	const closeTimerRef = useRef(null);
	const styleRef = useRef(null);

	const handleInfoOpen = useCallback(() => {
		setIsInfoOpen(true);
		if (closeTimerRef.current) {
			clearTimeout(closeTimerRef.current);
			closeTimerRef.current = null;
		}
	}, []);

	const handleInfoIconLeave = useCallback(() => {
		if (closeTimerRef.current) {
			clearTimeout(closeTimerRef.current);
		}
		closeTimerRef.current = setTimeout(() => {
			if (!bubbleHoverRef.current) {
				setIsInfoOpen(false);
			}
		}, 120);
	}, []);

	const handleBubbleEnter = useCallback(() => {
		bubbleHoverRef.current = true;
		if (closeTimerRef.current) {
			clearTimeout(closeTimerRef.current);
			closeTimerRef.current = null;
		}
		setIsInfoOpen(true);
	}, []);

	const handleBubbleLeave = useCallback(() => {
		bubbleHoverRef.current = false;
		setIsInfoOpen(false);
	}, []);

	const loadHistory = useCallback(async () => {
		try {
			setLoading(true);
			setError(null);
			const res = await fetch('/api/action-history');
			if (!res.ok) {
				throw new Error(`Request failed with status ${res.status}`);
			}
			const data = await res.json();
			const completed = Number(data?.completed);
			const requiredActions = Number(data?.required_actions);
			setHistory({
				actions: Array.isArray(data?.actions) ? data.actions : [],
				epoch: data?.epoch ?? null,
				completed: Number.isFinite(completed) ? completed : 0,
				required_actions: Number.isFinite(requiredActions) ? requiredActions : 0,
			});
		} catch (err) {
			console.error('[ActionHistory] Failed to load action history', err);
			setError('Unable to load action history right now.');
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		loadHistory();
	}, [loadHistory]);

	useEffect(() => {
		return () => {
			if (closeTimerRef.current) {
				clearTimeout(closeTimerRef.current);
			}
		};
	}, []);

	// Safely inject CSS styles using textContent (safer than dangerouslySetInnerHTML for CSS)
	useEffect(() => {
		if (styleRef.current) {
			styleRef.current.textContent = SCROLLBAR_STYLES;
		}
	}, []);

	const getTimestampValue = value => {
		const date = value ? new Date(value) : null;
		if (!date || Number.isNaN(date.getTime())) {
			return 0;
		}
		return date.getTime();
	};

	const entries = useMemo(() => {
		return [...history.actions]
			.filter(action => action && action.name)
			.sort((a, b) => getTimestampValue(b.timestamp) - getTimestampValue(a.timestamp))
			.map((action, index) => {
				const rawName = String(action.name || '');
				const normalized = rawName
					.replace(/[^a-z0-9]+/gi, '_')
					.replace(/_{2,}/g, '_')
					.replace(/^_|_$/g, '')
					.toUpperCase();
				const compactKey = normalized.replace(/_/g, '');
				const details =
					ACTION_DETAILS[normalized] ||
					ACTION_DETAILS[compactKey] || {
						title: formatActionName(normalized || 'Action'),
						description: 'Keeping busy to stay in top shape.',
						stat: null,
						statKey: null,
					};
				return {
					id: `${normalized || rawName}-${action.timestamp || index}`,
					title: details.title,
					description: details.description,
					stat: details.stat,
					statKey: details.statKey ?? null,
					time: formatTime(action.timestamp),
					isRightAligned: index % 2 === 0,
				};
			});
	}, [history.actions]);

	const handleBack = useCallback(() => {
		navigate('/dashboard');
	}, [navigate]);

	return (
		<div
			className="fixed inset-0 z-50 flex flex-col items-center overflow-hidden"
			style={{
				backgroundImage: `linear-gradient(135deg, rgba(49, 7, 85, 0.88), rgba(30, 27, 75, 0.88)), url(${backgroundMain})`,
				backgroundBlendMode: 'overlay',
				backgroundRepeat: 'no-repeat',
				backgroundPosition: 'center 10%',
				backgroundSize: 'cover',
				backgroundColor: '#2e1065',
			}}
		>
			<style ref={styleRef} />
			<div
				className="fixed inset-0"
				style={{
					backgroundImage: `url(${backgroundOverlay})`,
					backgroundSize: 'cover',
					backgroundPosition: 'center',
					opacity: 0.18,
					zIndex: 1,
				}}
			/>
			<div className="fixed inset-0 bg-gradient-to-br from-purple-950/60 via-indigo-950/50 to-purple-950/60" style={{ zIndex: 2 }} />

			<button
				type="button"
				onClick={handleBack}
				className="absolute top-4 left-4 z-[99] text-white transition-colors bg-white/10 hover:bg-white/15 hover:text-white rounded-full p-2 shadow-lg shadow-purple-900/40 border border-white/20 backdrop-blur"
				aria-label="Back to dashboard"
				title="Back to dashboard"
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					width="24"
					height="24"
					viewBox="0 0 24 24"
				>
					<path
						fill="currentColor"
						d="M20 11H7.83l5.59-5.59L12 4l-8 8l8 8l1.41-1.41L7.83 13H20z"
					/>
				</svg>
			</button>

			<div
				className="flex-1 flex flex-col items-center relative px-4 pb-12 w-full"
				style={{
					minHeight: '100vh',
					overflow: 'hidden',
					paddingTop: '24px',
					zIndex: 10,
				}}
			>
				<div className="flex flex-col items-center w-full max-w-[550px] mx-auto">
					<div className="text-center text-white/90">
						<h1 className="text-3xl font-bold tracking-tight">AI Action History</h1>
						<p className="mt-2 text-white/70 text-sm font-medium">
							Current Run's Actions
						</p>
						{Number.isFinite(history.completed) && Number.isFinite(history.required_actions) ? (
							<p className="mt-1 text-white/60 text-xs uppercase tracking-widest">
								{history.completed} actions completed
							</p>
						) : null}
						<p className="mt-4 text-white/60 text-xs sm:text-sm leading-relaxed max-w-[90%] mx-auto">
							Synced live with your Pett.AI account so agent-triggered events and main-app play sessions stay in lockstep.
						</p>
					</div>

					<div className="mt-6 w-full">
						<div className="bg-white/5 backdrop-blur-xl rounded-[32px] p-5 shadow-[0_18px_45px_rgba(2,6,23,0.45)] border border-white/10">
							<div className="flex items-center justify-between gap-3">
								<h2 className="text-white/80 text-lg font-semibold uppercase">Latest activity</h2>
								<div className="flex items-center gap-2">
									<div className="relative" onMouseEnter={handleInfoOpen} onMouseLeave={handleInfoIconLeave}>
										<button
											type="button"
											className="inline-flex items-center justify-center size-9 rounded-full bg-white/8 border border-white/15 text-white/80 hover:text-white hover:bg-white/12 transition-colors"
											onClick={handleInfoOpen}
											aria-label="About Pett.AI connection"
										>
											<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none">
												<path
													fill="currentColor"
													d="M10 18.333a8.333 8.333 0 1 0 0-16.666 8.333 8.333 0 0 0 0 16.666Zm-.833-11.25a.833.833 0 1 1 1.666 0a.833.833 0 0 1-1.666 0Zm0 2.5c0-.46.373-.833.833-.833c.46 0 .833.373.833.833V14.167a.833.833 0 1 1-1.666 0Z"
												/>
											</svg>
										</button>
										<div onMouseEnter={handleBubbleEnter} onMouseLeave={handleBubbleLeave} className={clsx(
											"absolute left-1/2 -top-3 -translate-x-1/2 -translate-y-full z-40 w-72 max-w-xs rounded-2xl bg-white/95 text-slate-900 shadow-xl shadow-purple-950/30 border border-white/60 p-4 text-xs leading-relaxed transition-all duration-200 origin-bottom",
											isInfoOpen ? "opacity-100 scale-100 pointer-events-auto" : "opacity-0 scale-95 pointer-events-none"
										)}>
											<p>
												These are the actions performed by the agent. You can directly play with your pet by logging in at the
												{' '}
												<a
													href="https://app.pett.ai"
													target="_blank"
													rel="noopener noreferrer"
													className="font-semibold text-purple-700 hover:text-purple-900"
												>
													main Pett.AI app
												</a>
												{' '}
												using the same sign-in method.
											</p>
											<div className="absolute left-1/2 bottom-0 translate-y-full -translate-x-1/2 w-3 h-3 rotate-45 bg-white/95 border-r border-b border-white/60" />
										</div>
									</div>
									<button
										type="button"
										onClick={loadHistory}
										className="text-white/80 text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 transition-all rounded-full px-4 py-1.5 shadow-inner shadow-black/20"
									>
										<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24"><path fill="currentColor" d="M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 0 0-8 8a8 8 0 0 0 8 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18a6 6 0 0 1-6-6a6 6 0 0 1 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z" /></svg>
									</button>
								</div>
							</div>

							<div className="mt-5 relative">
								<div
									className="pointer-events-none absolute inset-x-0 top-0 h-20 backdrop-blur-xl bg-blue-900/20 z-20 [mask-image:linear-gradient(to_bottom,black,transparent)]"
									aria-hidden="true"
								/>
								<div className="absolute left-1/2 top-4 bottom-4 w-px bg-white/10 hidden sm:block" aria-hidden="true" />
								<div className="action-history-scroll max-h-[60vh] overflow-y-auto overflow-x-hidden space-y-5 pr-1 pt-8 pb-12 relative z-10">
									{loading ? (
										<div className="flex items-center justify-center py-12 text-white/70 text-sm">
											Loading action history...
										</div>
									) : error ? (
										<div className="flex items-center justify-center py-12 text-red-200 text-sm">
											{error}
										</div>
									) : entries.length === 0 ? (
										<div className="flex items-center justify-center py-12 text-white/70 text-sm">
											No actions recorded yet today.
										</div>
									) : (
										entries.map((entry, index) => {
											const statStyle = entry.statKey ? STAT_STYLES[entry.statKey] : null;
											const bubbleClasses = clsx(
												'relative w-full sm:max-w-[340px] rounded-3xl px-4 py-3 shadow-xl border border-white/10 bg-gradient-to-br from-purple-950/70 via-purple-900/60 to-indigo-950/60 text-white/90 backdrop-blur-2xl',
												statStyle?.bubble,
											);
											const chipClasses = clsx(
												'mt-3 inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-wide border border-white/10 bg-white/5 text-white/90',
												statStyle?.chip,
											);

											// if its last entry, add mb-12
											const isLastEntry = index === entries.length - 1;
											const marginBottom = isLastEntry ? 'mb-12' : '';

											// if its first entry, add mt-8
											const isFirstEntry = index === 0;
											const marginTop = isFirstEntry ? 'mt-8' : '';

											return (
												<div
													key={entry.id}
													className={clsx('flex w-full', entry.isRightAligned ? 'justify-end' : 'justify-start', marginBottom, marginTop)}
												>
													<div className={clsx('flex flex-col gap-2 max-w-[340px]', entry.isRightAligned ? 'items-end text-right' : 'items-start text-left')}>
														<span className="text-[11px] font-medium tracking-widest text-white/50 uppercase">
															{entry.time}
														</span>
														<div className={bubbleClasses}>
															<span
																aria-hidden="true"
																className={clsx(
																	'absolute top-3 bottom-3 left-0 w-1 rounded-full',
																	statStyle?.accent || 'bg-white/20',
																)}
															/>
															{entry.statKey ? (() => {
																const iconElement = renderStatIcon(entry.statKey);
																return iconElement ? (
																	<span
																		className={clsx('absolute top-2 right-2 size-5 rounded-full flex items-center justify-center ring-1 ring-white/15 text-white', statStyle?.accent)}
																		title={entry.stat}
																	>
																		{iconElement}
																	</span>
																) : null;
															})() : null}
															<div className="flex flex-col gap-2">
																<p className="mr-6 text-sm font-semibold tracking-tight text-white/90">
																	{entry.title}
																</p>
																<p className="text-xs text-white/80 leading-relaxed break-words">
																	{entry.description}
																</p>
																{entry.stat ? <span className={chipClasses}>{entry.stat}</span> : null}
															</div>
														</div>
													</div>
												</div>
											);
										})
									)}
								</div>
								<div
									className="pointer-events-none absolute inset-x-0 bottom-0 h-20 backdrop-blur-xl bg-blue-900/20 z-20 [mask-image:linear-gradient(to_top,black,transparent)]"
									aria-hidden="true"
								/>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	);
};

export default ActionHistory;

