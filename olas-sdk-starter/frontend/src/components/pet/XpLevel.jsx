import React from 'react';
import './XpLevel.scss';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const formatNumber = num => {
	const n = Number(num);
	if (!Number.isFinite(n)) return '0';
	return Math.floor(n).toLocaleString();
};

const XpLevel = ({ level, xp, xpMin, xpMax, border = true, margin = true, padding = true, className = '' }) => {
	const hasAll = [level, xp, xpMin, xpMax].every(v => v !== undefined && v !== null && Number.isFinite(Number(v)));

	const current = Number(xp);
	const min = Number(xpMin);
	const max = Number(xpMax);
	const denom = max - min;
	const progressPct = Number.isFinite(current) && Number.isFinite(min) && Number.isFinite(max) && denom > 0
		? clamp(((current - min) / denom) * 100, 0, 100)
		: 0;

	if (!hasAll) {
		return null;
	}

	const containerClass = [
		'mt-2 level flex items-center gap-2 w-full rounded-2xl bg-white border-semantic-accent-muted',
		padding ? 'py-2 px-4' : '',
		margin ? 'mb-5' : '',
		border ? 'border' : '',
		className,
	]
		.filter(Boolean)
		.join(' ');

	return (
		<div className={containerClass}>
			<div className="text-base font-bold text-semantic-accent-bold">LVL {Number(level)}</div>
			<div className="level__progress relative rounded-full flex-grow overflow-hidden">
				<div className="level__progress--bar h-6 rounded-full" style={{ width: `${progressPct}%` }} />
				<div className="level__progress--text text-sm font-semibold text-white absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 drop-shadow-sm">
					{formatNumber(xp)}/{formatNumber(xpMax)}
				</div>
			</div>
		</div>
	);
};

export default XpLevel;


