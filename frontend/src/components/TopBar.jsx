import { Link, useNavigate } from "react-router-dom";
import { LogOut, User, Shield, LayoutGrid } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { Brand } from "./Brand";
import { Button } from "../components/ui/button";

export const TopBar = ({ children }) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="border-b border-slate-200 bg-white" data-testid="top-bar">
      <div className="flex min-h-14 flex-wrap items-center gap-x-2 gap-y-2 px-3 py-2 sm:gap-x-4 sm:px-5">
        <Link to="/projects" className="flex shrink-0 items-center gap-2" data-testid="brand-link">
          <Brand testid="topbar-brand" markClass="h-7 w-auto" wordClass="text-[13px] sm:text-[15px]" />
        </Link>
        {children && <div className="order-3 w-full min-w-0 sm:order-none sm:w-auto sm:flex-1">{children}</div>}
        <nav aria-label="Account navigation" className="ml-auto flex shrink-0 items-center gap-0.5">
          <Button variant="ghost" size="sm" className="text-xs h-8 px-2" title="Projects" aria-label="Projects" onClick={() => navigate("/projects")} data-testid="nav-projects">
            <LayoutGrid className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Projects</span>
          </Button>
          {user?.role === "admin" && (
            <Button variant="ghost" size="sm" className="text-xs h-8 px-2" title="Users" aria-label="Users" onClick={() => navigate("/admin")} data-testid="nav-admin">
              <Shield className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Users</span>
            </Button>
          )}
          <Button variant="ghost" size="sm" className="text-xs h-8 px-2" title="Profile" aria-label="Profile" onClick={() => navigate("/profile")} data-testid="nav-profile">
            <User className="h-3.5 w-3.5" />
            <span className="hidden max-w-28 truncate sm:inline">{user?.name || "Profile"}</span>
            <span className="hidden lg:inline ml-1.5 px-1.5 py-0.5 bg-slate-100 rounded-sm text-[10px] uppercase font-mono">
              {user?.role}
            </span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-xs h-8 rounded-sm"
            data-testid="logout-btn"
            aria-label="Sign out"
            title="Sign out"
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
          >
            <LogOut className="h-3.5 w-3.5" />
          </Button>
        </nav>
      </div>
    </header>
  );
};
