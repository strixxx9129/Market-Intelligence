import { Link, useNavigate } from 'react-router';

import { useAuth } from '../auth/AuthContext';
import { Card, PageTitle } from '../components/ui';
import { AuthForm } from './Login';

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="mx-auto max-w-sm">
      <PageTitle
        title="Create an account"
        subtitle="Save watchlists and alert rules, and create API keys with higher rate limits."
      />
      <Card>
        <AuthForm
          mode="register"
          onSubmit={async (email, password) => {
            await register(email, password);
            navigate('/account', { replace: true });
          }}
        />
        <p className="mt-4 text-sm">
          Already registered?{' '}
          <Link className="link" to="/login">
            Log in
          </Link>
        </p>
      </Card>
    </div>
  );
}
