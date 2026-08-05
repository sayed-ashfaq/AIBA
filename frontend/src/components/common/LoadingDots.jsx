import styles from "./LoadingDots.module.css";

export default function LoadingDots() {
  return (
    <div className={styles.dots} role="status" aria-label="Waiting for reply">
      <span />
      <span />
      <span />
    </div>
  );
}
