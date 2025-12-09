import React from 'react';
import PetStat from './PetStat';

const PetStats = ({ stats }) => {
	return (
		<div className="w-full grid grid-cols-5 gap-10 p-4 pt-3 rounded-2xl bg-white border-semantic-accent-muted" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)' }}>
			<PetStat type="hunger" value={stats?.hunger} />
			<PetStat type="health" value={stats?.health} />
			<PetStat type="energy" value={stats?.energy} />
			<PetStat type="happiness" value={stats?.happiness} />
			<PetStat type="hygiene" value={stats?.hygiene} />
		</div>
	);
};

export default PetStats;
