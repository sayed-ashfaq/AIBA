import styles from "./ErrorBanner.module.css";

export default function ErrorBanner({ message }) {
  if (!message) return null;
  return (
    <div className={styles.banner} role="alert">
      {message}
    </div>
  );
}
