import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Brand } from "../components/Brand";
import { useAuth } from "../context/AuthContext";
import { requestErrorMessage } from "../lib/api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", role: "engineer", org: "", contact: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await register(form);
      toast.success("Account created");
      navigate("/projects");
    } catch (err) {
      setError(requestErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen grid place-items-center bg-slate-50 p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-md bg-white border border-slate-200 rounded-sm p-7 space-y-4"
        data-testid="register-form"
      >
        <Brand markClass="h-9 w-auto" />
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Create account</h2>
          <p className="text-sm text-slate-500 mt-1">Register to start planning projects.</p>
        </div>
        {error && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-sm px-3 py-2" data-testid="register-error">
            {error}
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5 col-span-2">
            <Label className="text-xs uppercase tracking-wide text-slate-500">Full name</Label>
            <Input required value={form.name} onChange={set("name")} data-testid="register-name-input" className="rounded-sm" />
          </div>
          <div className="space-y-1.5 col-span-2">
            <Label className="text-xs uppercase tracking-wide text-slate-500">Email</Label>
            <Input required type="email" value={form.email} onChange={set("email")} data-testid="register-email-input" className="rounded-sm" />
          </div>
          <div className="space-y-1.5 col-span-2">
            <Label className="text-xs uppercase tracking-wide text-slate-500">Password</Label>
            <Input required type="password" minLength={6} value={form.password} onChange={set("password")} data-testid="register-password-input" className="rounded-sm" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase tracking-wide text-slate-500">Role</Label>
            <Select value={form.role} onValueChange={(v) => setForm((f) => ({ ...f, role: v }))}>
              <SelectTrigger className="rounded-sm" data-testid="register-role-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="engineer">Engineer</SelectItem>
                <SelectItem value="viewer">Viewer</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase tracking-wide text-slate-500">Organisation</Label>
            <Input value={form.org} onChange={set("org")} data-testid="register-org-input" className="rounded-sm" />
          </div>
        </div>
        <Button type="submit" disabled={busy} className="w-full rounded-sm" data-testid="register-submit-button">
          {busy ? "Creating…" : "Create account"}
        </Button>
        <p className="text-sm text-slate-500">
          Already registered?{" "}
          <Link to="/login" className="text-blue-600 hover:underline" data-testid="go-login-link">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
