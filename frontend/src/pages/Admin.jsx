import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, apiError } from "../lib/api";
import { TopBar } from "../components/TopBar";
import { Section } from "../components/Field";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { dt } from "../lib/format";

export default function Admin() {
  const [users, setUsers] = useState([]);

  useEffect(() => {
    api
      .get("/users")
      .then(({ data }) => setUsers(data))
      .catch((e) => toast.error(apiError(e.response?.data?.detail)));
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="max-w-5xl mx-auto p-6 space-y-4" data-testid="admin-page">
        <h1 className="text-2xl font-semibold tracking-tight">Platform users</h1>
        <Section title={`Registered users (${users.length})`} testid="admin-users-section">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Organisation</TableHead>
                <TableHead>Role</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id} data-testid={`admin-user-${u.id}`}>
                  <TableCell className="py-2">{u.name}</TableCell>
                  <TableCell className="py-2 font-mono text-xs">{u.email}</TableCell>
                  <TableCell className="py-2">{u.org || "—"}</TableCell>
                  <TableCell className="py-2 uppercase font-mono text-xs">{u.role}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>
      </main>
    </div>
  );
}
