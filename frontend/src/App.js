import "./App.css";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "./components/ui/sonner";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { BrandMark } from "./components/Brand";
import ErrorBoundary from "./components/ErrorBoundary";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Projects from "./pages/Projects";
import Profile from "./pages/Profile";
import Workspace from "./pages/Workspace";
import Admin from "./pages/Admin";
import AppBackground from "./components/AppBackground";

const Protected = ({ children }) => {
  const { user } = useAuth();
  if (user === null)
    return (
      <div className="min-h-screen grid place-items-center" data-testid="auth-loading">
        <div className="flex flex-col items-center gap-3">
          <BrandMark className="h-12 w-auto animate-pulse" />
          <span className="text-sm text-slate-500">Loading Aptimizer…</span>
        </div>
      </div>
    );
  if (user === false) return <Navigate to="/login" replace />;
  return children;
};

function App() {
  return (
    <div className="App">
      {/* Mounted outside the router so navigation never restarts the grid. */}
      <AppBackground />
      <AuthProvider>
        <BrowserRouter>
          <ErrorBoundary title="Application Error">
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
              <Route
                path="/projects"
                element={
                  <Protected>
                    <Projects />
                  </Protected>
                }
              />
              <Route
                path="/projects/:projectId"
                element={
                  <Protected>
                    <Workspace />
                  </Protected>
                }
              />
              <Route
                path="/profile"
                element={
                  <Protected>
                    <Profile />
                  </Protected>
                }
              />
              <Route
                path="/admin"
                element={
                  <Protected>
                    <Admin />
                  </Protected>
                }
              />
              <Route path="*" element={<Navigate to="/projects" replace />} />
            </Routes>
          </ErrorBoundary>
        </BrowserRouter>
        <Toaster position="top-right" />
      </AuthProvider>
    </div>
  );
}

export default App;
