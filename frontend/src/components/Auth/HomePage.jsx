import AuthPanel from "./AuthPanel";
import ChatShowcase from "./ChatShowcase";
import styles from "./HomePage.module.css";

export default function HomePage({ auth }) {
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <h1>NL2SQL</h1>
          <span>Talk with your database</span>
        </div>
      </header>

      <main className={styles.columns}>
        <section className={styles.authColumn}>
          <AuthPanel auth={auth} />
        </section>
        <section className={styles.showcaseColumn}>
          <ChatShowcase />
        </section>
      </main>
    </div>
  );
}
