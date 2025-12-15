import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../providers/AuthProvider';

const AllSet = () => {
	const { authenticated, ready } = useAuth();
	const navigate = useNavigate();

	useEffect(() => {
		if (!ready) return;
		if (authenticated) {
			navigate('/dashboard', { replace: true });
		} else {
			navigate('/login', { replace: true });
		}
	}, [authenticated, navigate, ready]);

	return null;
};

export default AllSet;
