import ChatApp from "./components/ChatApp";
import HomePage from "./components/Auth/HomePage";
import LoadingDots from "./components/common/LoadingDots";
import { useAuth } from "./hooks/useAuth";
import styles from "./App.module.css";

export default function App() {
  const auth = useAuth();

  if (auth.isLoading) {
    return (
      <div className={styles.splash}>
        <LoadingDots />
      </div>
    );
  }

  if (!auth.user) {
    return <HomePage auth={auth} />;
  }

  return <ChatApp auth={auth} />;
}
