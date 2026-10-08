import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "../lib/api";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null); // null = checking, false = anonymous

  const bootstrap = useCallback(async () => {
    // A 401 means signed out. Anything else -- the hosted server restarting, a dropped
    // connection -- is retried a couple of times first, so a brief outage does not throw a
    // signed-in user back to the login page.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const { data } = await api.get("/auth/me");
        setUser(data);
        return;
      } catch (err) {
        const status = err?.response?.status;
        if (status === 401 || status === 403 || !localStorage.getItem("aptimizer_token")) break;
        await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
      }
    }
    setUser(false);
  }, []);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    localStorage.setItem("aptimizer_token", data.access_token);
    setUser(data.user);
    return data.user;
  };

  const register = async (payload) => {
    const { data } = await api.post("/auth/register", payload);
    localStorage.setItem("aptimizer_token", data.access_token);
    setUser(data.user);
    return data.user;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      /* ignore */
    }
    localStorage.removeItem("aptimizer_token");
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, setUser, login, register, logout }}>{children}</AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
