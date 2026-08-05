import { useCallback, useEffect, useState } from "react";
import * as api from "../api/client";

export function useConnections() {
  const [connections, setConnections] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setConnections(await api.listConnections());
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const activate = useCallback(
    async (id) => {
      await api.activateConnection(id);
      await refresh();
    },
    [refresh],
  );

  const create = useCallback(
    async (payload) => {
      await api.createConnection(payload);
      await refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (id) => {
      await api.deleteConnection(id);
      await refresh();
    },
    [refresh],
  );

  const active = connections.find((c) => c.active) ?? null;

  return { connections, active, isLoading, error, activate, create, remove };
}
