import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Building2, Pencil, Plus, Trash2, Users } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { TopBar } from "../components/TopBar";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { dt, num } from "../lib/format";

const EMPTY = { name: "", client: "", location: "", plot_reference: "", latitude: "", longitude: "" };

export default function Projects() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState([]);
  const [cities, setCities] = useState([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const load = async () => {
    try {
      const { data } = await api.get("/projects");
      setProjects(data);
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    }
  };

  useEffect(() => {
    load();
    api.get("/cities")
      .then(({ data }) => setCities(data?.cities || []))
      .catch((err) => {
        console.warn("Could not load city list:", err);
      });
  }, []);

  const openNew = (v) => {
    setForm(EMPTY);
    setOpen(v);
  };

  const create = async () => {
    if (!form.name.trim()) return toast.error("Project name is required");
    if ((form.latitude && !form.longitude) || (!form.latitude && form.longitude))
      return toast.error("Enter both latitude and longitude, or leave both blank");
    setBusy(true);
    try {
      const payload = {
        ...form,
        latitude: form.latitude !== "" ? Number(form.latitude) : null,
        longitude: form.longitude !== "" ? Number(form.longitude) : null,
      };
      const { data } = await api.post("/projects", payload);
      toast.success("Project created");
      setForm(EMPTY);
      setOpen(false);
      navigate(`/projects/${data.id}`);
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const saveEdit = async () => {
    setBusy(true);
    try {
      const { name, client, location, plot_reference, status } = editing;
      await api.put(`/projects/${editing.id}`, { updates: { name, client, location, plot_reference, status } });
      toast.success("Project updated");
      setEditing(null);
      load();
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      await api.delete(`/projects/${confirmDelete.id}`);
      toast.success("Project deleted");
      setConfirmDelete(null);
      load();
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    }
  };

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="max-w-7xl mx-auto p-6 space-y-6" data-testid="projects-page">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">Projects</h1>
            <p className="text-sm text-slate-500 mt-1">
              {projects.length} project{projects.length === 1 ? "" : "s"} · signed in as{" "}
              <span className="font-mono">{user?.email}</span>
            </p>
          </div>
          {user?.role !== "viewer" && (
            <Dialog open={open} onOpenChange={openNew}>
              <Button className="rounded-sm" data-testid="new-project-button" onClick={() => openNew(true)}>
                <Plus className="h-4 w-4 mr-1.5" /> New project
              </Button>
              <DialogContent className="bg-white">
                <DialogHeader>
                  <DialogTitle>New project</DialogTitle>
                  <DialogDescription>Metadata for the project. The plot boundary and setbacks are drawn after creation.</DialogDescription>
                </DialogHeader>
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase tracking-wide text-slate-500">Project name</Label>
                    <Input className="rounded-sm" placeholder="Green Meadows Phase 1" data-testid="project-name-input"
                      value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase tracking-wide text-slate-500">Client</Label>
                    <Input className="rounded-sm" placeholder="Meadow Developers Pvt Ltd" data-testid="project-client-input"
                      value={form.client} onChange={(e) => setForm((f) => ({ ...f, client: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase tracking-wide text-slate-500">Location (city)</Label>
                    <Select value={form.location} onValueChange={(v) => setForm((f) => ({ ...f, location: v }))}>
                      <SelectTrigger className="rounded-sm" data-testid="project-location-select">
                        <SelectValue placeholder="Select a city" />
                      </SelectTrigger>
                      <SelectContent className="max-h-72">
                        {cities.map((c) => (
                          <SelectItem key={c.city} value={c.city}>{c.city} — {c.state}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <p className="text-[11px] text-slate-500">
                      Sets the map centre, seismic zone, wind speed and rainfall for this project.
                    </p>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase tracking-wide text-slate-500">Plot / survey reference</Label>
                    <Input className="rounded-sm" placeholder="Survey No. 42/1B" data-testid="project-plot_reference-input"
                      value={form.plot_reference} onChange={(e) => setForm((f) => ({ ...f, plot_reference: e.target.value }))} />
                    <p className="text-[11px] text-slate-500">
                      A label for your own records only -- it does not set the map location.
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label className="text-xs uppercase tracking-wide text-slate-500">Latitude</Label>
                      <Input className="rounded-sm" type="number" step="any" placeholder="17.4239" data-testid="project-latitude-input"
                        value={form.latitude} onChange={(e) => setForm((f) => ({ ...f, latitude: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs uppercase tracking-wide text-slate-500">Longitude</Label>
                      <Input className="rounded-sm" type="number" step="any" placeholder="78.4738" data-testid="project-longitude-input"
                        value={form.longitude} onChange={(e) => setForm((f) => ({ ...f, longitude: e.target.value }))} />
                    </div>
                    <p className="text-[11px] text-slate-500 col-span-2">
                      GPS coordinates of the plot (from Google Maps or a survey sketch) -- sets the exact map
                      centre. Leave blank to start from the selected city's centre instead.
                    </p>
                  </div>
                </div>
                <DialogFooter>
                  <Button onClick={create} disabled={busy} className="rounded-sm" data-testid="create-project-submit">
                    {busy ? "Creating…" : "Create project"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          )}
        </div>

        {projects.length === 0 ? (
          <div className="border border-slate-200 bg-white rounded-sm overflow-hidden grid md:grid-cols-2" data-testid="projects-empty">
            <div className="p-10">
              <h2 className="text-lg font-semibold tracking-tight">No projects yet</h2>
              <p className="text-sm text-slate-500 mt-2 max-w-sm">
                Create your first project to draw the plot boundary, apply setbacks, generate the site layout and
                run GIS site analysis.
              </p>
            </div>
            <img
              src="https://images.pexels.com/photos/18153132/pexels-photo-18153132.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
              alt="Apartment building"
              className="h-56 md:h-full w-full object-cover"
            />
          </div>
        ) : (
          <div className="grid gap-px bg-slate-200 border border-slate-200 md:grid-cols-2 xl:grid-cols-3" data-testid="projects-grid">
            {projects.map((p) => (
              <article
                key={p.id}
                className="bg-white p-4 hover:bg-slate-50 transition-colors cursor-pointer"
                onClick={() => navigate(`/projects/${p.id}`)}
                data-testid={`project-card-${p.id}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="font-semibold tracking-tight truncate">{p.name}</h3>
                    <p className="text-xs text-slate-500 truncate">
                      {[p.client, p.location].filter(Boolean).join(" · ") || "Site project"}
                    </p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {p.shared && (
                      <span className="text-[10px] font-mono uppercase bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded-sm flex items-center gap-1">
                        <Users className="h-3 w-3" /> shared
                      </span>
                    )}
                    <span className="text-[10px] font-mono uppercase bg-slate-100 px-1.5 py-0.5 rounded-sm">{p.status}</span>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-px bg-slate-200 border border-slate-200">
                  {[
                    ["Plot m²", num(p.summary.plot_area_sqm, 0)],
                    ["Acres", num((p.summary.plot_area_sqm || 0) / 4046.86, 3)],
                    ["FAR", num(p.summary.far, 2)],
                  ].map(([k, v]) => (
                    <div key={k} className="bg-white px-2 py-1.5">
                      <div className="text-[9px] uppercase tracking-wider text-slate-500">{k}</div>
                      <div className="font-mono text-sm">{v}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between text-[11px] text-slate-500">
                  <span className="flex items-center gap-1">
                    <Building2 className="h-3 w-3" /> {p.summary.towers} tower(s) · edited {dt(p.updated_at)}
                  </span>
                  {!p.shared && (
                    <span className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
                      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-slate-600 hover:text-slate-900"
                        data-testid={`edit-project-${p.id}`} title="Edit project details"
                        onClick={() => setEditing({ ...p, status: p.status || "draft" })}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-red-600 hover:text-red-700"
                        data-testid={`delete-project-${p.id}`} title="Delete project"
                        onClick={() => setConfirmDelete(p)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </span>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </main>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="bg-white">
          <DialogHeader>
            <DialogTitle>Edit project</DialogTitle>
            <DialogDescription>Project details. The plot itself is edited inside the project.</DialogDescription>
          </DialogHeader>
          {editing && (
            <div className="space-y-3">
              {[["name", "Project name"], ["client", "Client"], ["plot_reference", "Plot / survey reference"]].map(([key, label]) => (
                <div key={key} className="space-y-1.5">
                  <Label className="text-xs uppercase tracking-wide text-slate-500">{label}</Label>
                  <Input className="rounded-sm" data-testid={`edit-project-${key}-input`} value={editing[key] || ""}
                    onChange={(e) => setEditing((f) => ({ ...f, [key]: e.target.value }))} />
                </div>
              ))}
              <div className="space-y-1.5">
                <Label className="text-xs uppercase tracking-wide text-slate-500">Location (city)</Label>
                <Select value={editing.location || ""} onValueChange={(v) => setEditing((f) => ({ ...f, location: v }))}>
                  <SelectTrigger className="rounded-sm" data-testid="edit-project-location-select">
                    <SelectValue placeholder="Select a city" />
                  </SelectTrigger>
                  <SelectContent className="max-h-72">
                    {cities.map((c) => (
                      <SelectItem key={c.city} value={c.city}>{c.city} — {c.state}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs uppercase tracking-wide text-slate-500">Status</Label>
                <Select value={editing.status} onValueChange={(v) => setEditing((f) => ({ ...f, status: v }))}>
                  <SelectTrigger className="rounded-sm" data-testid="edit-project-status-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["draft", "active", "on_hold", "completed"].map((s) => (
                      <SelectItem key={s} value={s}>{s.replace("_", " ")}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" className="rounded-sm" onClick={() => setEditing(null)}>Cancel</Button>
            <Button onClick={saveEdit} disabled={busy || !editing?.name?.trim()} className="rounded-sm" data-testid="edit-project-submit">
              {busy ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!confirmDelete} onOpenChange={(v) => !v && setConfirmDelete(null)}>
        <DialogContent className="bg-white">
          <DialogHeader>
            <DialogTitle>Delete project?</DialogTitle>
            <DialogDescription>
              “{confirmDelete?.name}” and its site layout will be permanently deleted. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="rounded-sm" onClick={() => setConfirmDelete(null)}>Cancel</Button>
            <Button variant="destructive" className="rounded-sm" onClick={remove} data-testid="confirm-delete-project">
              <Trash2 className="h-3.5 w-3.5 mr-1.5" /> Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
