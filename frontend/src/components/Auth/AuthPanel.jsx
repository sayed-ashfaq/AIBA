import { useState } from "react";
import { googleLoginUrl } from "../../api/client";
import { GoogleIcon } from "../common/icons";
import styles from "./AuthPanel.module.css";

export default function AuthPanel({ auth }) {
  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const isLogin = mode === "login";

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      if (isLogin) {
        await auth.login(email, password);
      } else {
        await auth.signup(email, password, fullName);
      }
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
      setIsSubmitting(false);
    }
  };

  const switchMode = () => {
    setMode(isLogin ? "signup" : "login");
    setError(null);
  };

  return (
    <div className={styles.card}>
      <h2 className={styles.title}>{isLogin ? "Welcome back" : "Create your account"}</h2>
      <p className={styles.subtitle}>
        {isLogin ? "Log in to talk with your database." : "Sign up to start talking with your database."}
      </p>

      <button type="button" className={styles.googleButton} onClick={() => (window.location.href = googleLoginUrl())}>
        <GoogleIcon />
        Continue with Google
      </button>

      <div className={styles.divider}>
        <span>or</span>
      </div>

      <form className={styles.form} onSubmit={handleSubmit}>
        {!isLogin && (
          <input
            type="text"
            placeholder="Full name (optional)"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className={styles.input}
            autoComplete="name"
          />
        )}
        <input
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={styles.input}
          autoComplete="email"
        />
        <input
          type="password"
          required
          minLength={isLogin ? undefined : 8}
          placeholder={isLogin ? "Password" : "Password (min. 8 characters)"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={styles.input}
          autoComplete={isLogin ? "current-password" : "new-password"}
        />

        {error && <p className={styles.error}>{error}</p>}

        <button type="submit" className={styles.submitButton} disabled={isSubmitting}>
          {isSubmitting ? "Please wait…" : isLogin ? "Log in" : "Create account"}
        </button>
      </form>

      <p className={styles.switchMode}>
        {isLogin ? "Don't have an account?" : "Already have an account?"}{" "}
        <button type="button" className={styles.switchButton} onClick={switchMode}>
          {isLogin ? "Sign up" : "Log in"}
        </button>
      </p>
    </div>
  );
}
