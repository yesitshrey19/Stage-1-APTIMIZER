import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useAuth } from "../context/AuthContext";
import { api, apiError } from "../lib/api";
import { TopBar } from "../components/TopBar";
import { Section, TextField } from "../components/Field";
import { Button } from "../components/ui/button";

export default function Profile() {
  const { user, setUser } = useAuth();
  const [form, setForm] = useState({ name: "", org: "", contact: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) setForm({ name: user.name || "", org: user.org || "", contact: user.contact || "" });
  }, [user]);

  const save = async () => {
    setBusy(true);
    try {
      const { data } = await api.put("/users/me", form);
      setUser(data);
      toast.success("Profile updated");
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="max-w-3xl mx-auto p-6 space-y-4" data-testid="profile-page">
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <Section title="Account details" testid="profile-section">
          <div className="grid sm:grid-cols-2 gap-4">
            <TextField label="Name" value={form.name} onChange={(v) => setForm((f) => ({ ...f, name: v }))} testid="profile-name-input" />
            <TextField label="Organisation" value={form.org} onChange={(v) => setForm((f) => ({ ...f, org: v }))} testid="profile-org-input" />
            <TextField label="Contact" value={form.contact} onChange={(v) => setForm((f) => ({ ...f, contact: v }))} testid="profile-contact-input" />
            <div className="space-y-1">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Email</div>
              <div className="font-mono text-sm h-9 flex items-center" data-testid="profile-email">{user?.email}</div>
            </div>
            <div className="space-y-1">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Role</div>
              <div className="font-mono text-sm h-9 flex items-center uppercase" data-testid="profile-role">{user?.role}</div>
            </div>
          </div>
          <div className="mt-4">
            <Button onClick={save} disabled={busy} className="rounded-sm" data-testid="profile-save-button">
              {busy ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </Section>
      </main>
    </div>
  );
}
