import { useEffect, useRef, useState } from "react";
import styles from "./UserMenu.module.css";

function initialOf(user) {
  return (user.full_name || user.email || "?").trim().charAt(0).toUpperCase();
}

export default function UserMenu({ user, onLogout }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handleClickOutside = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setIsOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  const handleLogout = async () => {
    setIsLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setIsLoggingOut(false);
    }
  };

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setIsOpen((v) => !v)}
        aria-label="Account menu"
      >
        {user.avatar_url ? (
          <img src={user.avatar_url} alt="" className={styles.avatarImg} />
        ) : (
          <span className={styles.avatar}>{initialOf(user)}</span>
        )}
      </button>

      {isOpen && (
        <div className={styles.panel}>
          <div className={styles.info}>
            <div className={styles.name}>{user.full_name || "Account"}</div>
            <div className={styles.email}>{user.email}</div>
          </div>
          <button type="button" className={styles.logoutButton} onClick={handleLogout} disabled={isLoggingOut}>
            {isLoggingOut ? "Logging out…" : "Log out"}
          </button>
        </div>
      )}
    </div>
  );
}
