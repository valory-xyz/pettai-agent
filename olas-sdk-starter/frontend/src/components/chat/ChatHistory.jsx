import React, { useEffect, useMemo, useRef } from 'react';
import clsx from 'clsx';

// Simple text formatter - no HTML needed, CSS will handle line breaks
const formatMessage = text => {
	if (!text) return '';
	// Return plain text - CSS white-space: pre-line will handle newlines
	return String(text);
};

const normalizeTimestamp = timestamp => {
	if (typeof timestamp !== 'number' || Number.isNaN(timestamp)) {
		return Date.now();
	}

	// Treat values <= 10^11 as seconds and convert to milliseconds
	return timestamp > 1e11 ? timestamp : timestamp * 1000;
};

const formatTimestamp = timestamp => {
	const date = new Date(normalizeTimestamp(timestamp));
	if (Number.isNaN(date.getTime())) {
		return '—';
	}

	try {
		return new Intl.DateTimeFormat([], {
			hour: 'numeric',
			minute: '2-digit',
		}).format(date);
	} catch (_error) {
		return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
	}
};

const ChatHistory = ({ messages = [], isVisible, onClose, onPlayAudio }) => {
	const containerRef = useRef(null);

	const preparedMessages = useMemo(() => {
		return messages
			.filter(Boolean)
			.map(message => ({
				...message,
				formattedMessage: formatMessage(message.message ?? ''),
				label: formatTimestamp(message.timestamp),
			}));
	}, [messages]);

	useEffect(() => {
		if (isVisible && containerRef.current) {
			containerRef.current.scrollTop = containerRef.current.scrollHeight;
		}
	}, [isVisible, preparedMessages]);

	if (!isVisible) {
		return null;
	}

	return (
		<>
			<div
				ref={containerRef}
				className="bg-white/95 backdrop-blur-md rounded-2xl p-4 max-h-64 overflow-y-auto mb-4 shadow-2xl animate-slide-up"
			>
				<div className="space-y-3">
					{preparedMessages.map(message => (
						<div
							key={message.id}
							className={clsx(
								'p-3 rounded-lg',
								message.sender === 'user' ? 'bg-purple-100 ml-8' : 'bg-gray-100 mr-8',
							)}
						>
							<div className="flex justify-between items-start gap-2">
								<div className="flex-1">
									<p className="text-sm text-gray-800 whitespace-pre-line">
										{message.formattedMessage}
									</p>
									<p className="text-xs text-gray-500 mt-1">{message.label}</p>
								</div>
								{Array.isArray(message.audio) && message.sender === 'pet' && onPlayAudio && (
									<button
										onClick={() => onPlayAudio(message.audio, message.id)}
										className="ml-2 p-1 text-purple-600 hover:text-purple-800 transition-colors"
										title="Play audio"
										aria-label="Play message audio"
										type="button"
									>
										<svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
											<path
												fillRule="evenodd"
												d="M10 18a8 8 0 100-16 8 8 0 000 16zm-.445-10.832A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"
												clipRule="evenodd"
											/>
										</svg>
									</button>
								)}
							</div>
						</div>
					))}
				</div>
			</div>

			<button
				onClick={onClose}
				className="w-full bg-white/80 backdrop-blur-sm hover:bg-white/90 transition-all rounded-2xl px-5 py-2 shadow-lg text-center text-sm text-gray-600 font-medium mb-3"
				aria-label="Hide message history"
				type="button"
			>
				Hide History ▼
			</button>
		</>
	);
};

export default ChatHistory;



