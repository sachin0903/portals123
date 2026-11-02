"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { UserPlus, Pencil, Trash2, Loader2, Users } from "lucide-react";

const AGENTS = ["Portfolio Agent", "Advisor Agent", "Report Agent", "Compliance Agent"];

const roleColors: Record<string, string> = {
  admin:  "bg-red-500/10 text-red-400 border-red-500/20",
  editor: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  member: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  viewer: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
};

interface User {
  id: string;
  name: string;
  slackUserId: string;
  platform: string;
  agents: string[];
  role: string;
  status: string;
  createdAt: string;
}

const EMPTY: Omit<User, "id" | "createdAt" | "status"> = {
  name: "",
  slackUserId: "",
  platform: "slack",
  agents: [],
  role: "member",
};

// ---------------------------------------------------------------------------
// User dialog (create + edit)
// ---------------------------------------------------------------------------

function UserDialog({
  user,
  onClose,
  onSaved,
}: {
  user: User | null;
  onClose: () => void;
  onSaved: (u: User) => void;
}) {
  const isEdit = !!user?.id;
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (user) {
      setForm({ name: user.name, slackUserId: user.slackUserId, platform: user.platform, agents: user.agents, role: user.role });
    } else {
      setForm({ ...EMPTY });
    }
    setError("");
  }, [user]);

  function toggleAgent(agent: string) {
    setForm((f) => ({
      ...f,
      agents: f.agents.includes(agent) ? f.agents.filter((a) => a !== agent) : [...f.agents, agent],
    }));
  }

  async function handleSave() {
    if (!form.name.trim() || !form.slackUserId.trim()) {
      setError("Name and Slack User ID are required.");
      return;
    }
    setSaving(true);
    setError("");

    const url = isEdit ? `/api/users/${user!.id}` : "/api/users";
    const method = isEdit ? "PUT" : "POST";

    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    const data = await res.json();
    setSaving(false);
    if (!res.ok) { setError(data.error); return; }
    onSaved(data);
    onClose();
  }

  return (
    <Dialog open onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit User" : "Add User"}</DialogTitle>
          <DialogDescription>
            Map a Slack user to the agents they can access via DM.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>Full Name</Label>
            <Input
              placeholder="e.g. John Trader"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </div>

          <div className="space-y-2">
            <Label>Slack User ID</Label>
            <Input
              placeholder="e.g. U0B8UCTE8BD"
              className="font-mono"
              value={form.slackUserId}
              onChange={(e) => setForm((f) => ({ ...f, slackUserId: e.target.value.trim() }))}
            />
            <p className="text-xs text-muted-foreground">
              Find it in Slack: click on the user profile → ··· → Copy member ID
            </p>
          </div>

          <div className="space-y-2">
            <Label>Role</Label>
            <Select value={form.role} onValueChange={(v) => setForm((f) => ({ ...f, role: v || "member" }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">viewer — read-only</SelectItem>
                <SelectItem value="member">member — query agents</SelectItem>
                <SelectItem value="editor">editor — create reports</SelectItem>
                <SelectItem value="admin">admin — full access</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Allowed Agents</Label>
            <div className="flex flex-wrap gap-2">
              {AGENTS.map((agent) => (
                <button
                  key={agent}
                  type="button"
                  onClick={() => toggleAgent(agent)}
                  className={`px-3 py-1 rounded-full text-xs border transition-colors ${
                    form.agents.includes(agent)
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-muted text-muted-foreground border-border hover:border-primary/50"
                  }`}
                >
                  {agent}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">Select which agents this user can access in DMs.</p>
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {isEdit ? "Save" : "Add User"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState<User | null | "new">(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/users")
      .then((r) => r.json())
      .then((d) => { setUsers(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  async function handleDelete(id: string) {
    setDeleting(id);
    await fetch(`/api/users/${id}`, { method: "DELETE" });
    setUsers((prev) => prev.filter((u) => u.id !== id));
    setDeleting(null);
  }

  function handleSaved(user: User) {
    setUsers((prev) => {
      const idx = prev.findIndex((u) => u.id === user.id);
      return idx === -1 ? [...prev, user] : prev.map((u) => (u.id === user.id ? user : u));
    });
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Users & Access</h1>
          <p className="text-muted-foreground">Map Slack users to agents for DM-based access control.</p>
        </div>
        <Button onClick={() => setDialog("new")}>
          <UserPlus className="h-4 w-4 mr-2" /> Add User
        </Button>
      </div>

      {users.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <Users className="h-10 w-10 text-muted-foreground mb-4" />
            <p className="font-medium mb-1">No users configured</p>
            <p className="text-sm text-muted-foreground mb-4">
              Add users to control which agents they can access via Slack DMs.
            </p>
            <Button onClick={() => setDialog("new")}>
              <UserPlus className="h-4 w-4 mr-2" /> Add First User
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Slack User ID</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Allowed Agents</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow key={user.id}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <Avatar className="h-8 w-8">
                          <AvatarFallback className="text-xs bg-muted">
                            {user.name.split(" ").map((n) => n[0]).join("").slice(0, 2)}
                          </AvatarFallback>
                        </Avatar>
                        <p className="font-medium text-sm">{user.name}</p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{user.slackUserId}</code>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-xs border ${roleColors[user.role] ?? ""}`}>
                        {user.role}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {user.agents.length === 0 ? (
                          <span className="text-xs text-muted-foreground">None</span>
                        ) : (
                          user.agents.map((a) => (
                            <Badge key={a} variant="secondary" className="text-xs">{a}</Badge>
                          ))
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <div className={`h-1.5 w-1.5 rounded-full ${user.status === "active" ? "bg-emerald-500" : "bg-zinc-500"}`} />
                        <span className="text-xs text-muted-foreground">{user.status}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setDialog(user)}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-destructive hover:bg-destructive/10"
                          disabled={deleting === user.id}
                          onClick={() => handleDelete(user.id)}
                        >
                          {deleting === user.id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <Trash2 className="h-3.5 w-3.5" />}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card className="border-blue-500/20 bg-blue-500/5">
        <CardContent className="pt-4 text-xs text-muted-foreground space-y-1">
          <p>• Users listed here can DM the bot directly without needing to @mention it in a channel.</p>
          <p>• RBAC is enforced by Graphene — Slack workspace permissions do not override this list.</p>
          <p>• Channel interactions use Channel Binding rules, not this list.</p>
        </CardContent>
      </Card>

      {dialog !== null && (
        <UserDialog
          user={dialog === "new" ? null : dialog}
          onClose={() => setDialog(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
