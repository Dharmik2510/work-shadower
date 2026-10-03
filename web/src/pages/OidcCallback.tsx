import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { completeOidcLogin } from "../auth/oidc";
import { Spinner } from "../components/bits";

export default function OidcCallback() {
  const { signIn } = useAuth();
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return;
    done.current = true;
    completeOidcLogin(window.location.search)
      .then(async ({ idToken, returnTo }) => {
        await signIn(idToken);
        nav(returnTo, { replace: true });
      })
      .catch((e: Error) => setError(e.message));
  }, [nav, signIn]);

  return (
    <div className="login">
      <div className="login__panel">
        {error ? (
          <>
            <h1>Sign-in didn't finish</h1>
            <p className="login__lede">{error}</p>
            <Link className="btn btn--primary" to="/login">
              Back to sign in
            </Link>
          </>
        ) : (
          <Spinner label="Finishing sign-in" />
        )}
      </div>
    </div>
  );
}
