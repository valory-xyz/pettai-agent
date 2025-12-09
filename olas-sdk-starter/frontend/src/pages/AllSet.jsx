import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../providers/AuthProvider';

const AllSet = () => {
	const { authenticated } = useAuth();
	const navigate = useNavigate();

	useEffect(() => {
		if (authenticated) {
			navigate('/dashboard', { replace: true });
		} else {
			navigate('/login', { replace: true });
		}
	}, [authenticated, navigate]);

	return null;
};

export default AllSet;
