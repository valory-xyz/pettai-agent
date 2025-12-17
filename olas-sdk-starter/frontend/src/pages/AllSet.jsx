import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../providers/AuthProvider';

const AllSet = () => {
	const { authenticated } = useAuth();
	const navigate = useNavigate();

	useEffect(() => {
		// wait a bit before navigating
		setTimeout(() => {
			if (authenticated) {
				navigate('/dashboard', { replace: true });
			} else {
				navigate('/login', { replace: true });
			}
		}, 200);
	}, [authenticated, navigate]);

	return null;
};

export default AllSet;
