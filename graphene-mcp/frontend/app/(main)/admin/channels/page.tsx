"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Plus, Pencil, Trash2, GitBranch, Loader2, Unplug } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Binding {
  id: string;
  channelId: string;
  channelName: string;
  provider: string;
  providerId: string;
  agent: string;
  scope: string;
  createdBy: string;
  updatedAt: string;
}

interface Provider {
  id: string;
  name: string;
  type: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const scopeColors: Record<string, string> = {
  org:   "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  team:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  admin: "bg-red-500/10 text-red-400 border-red-500/20",
};

const AGENTS = [
  "Portfolio Agent",
  "Advisor Agent",
  "Report Agent",
  "Compliance Agent",
];

// ---------------------------------------------------------------------------
// Binding form (shared by create + edit)
// ---------------------------------------------------------------------------

function BindingForm({
  form,
  providers,
  onChange,
}: {
  form: Partial<Binding>;
  providers: Provider[];
  onChange: (f: Partial<Binding>) => void;
}) {
  return (
    <div className="space-y-4 py-2">
      <div className="space-y-2">
        <Label>Provider</Label>
        <Select
          value={form.providerId ?? ""}
          onValueChange={(v) => {
            const val = v || "";
            const p = providers.find((x) => x.id === val);
            onChange({ ...form, providerId: val, provider: p?.name ?? "" });
          }}
        >
          <SelectTrigger><SelectValue placeholder="Select provider…" /></SelectTrigger>
          <SelectContent>
            {providers.map((p) => (
              <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Channel ID</Label>
        <Input
          placeholder="e.g. C04PORTFOLIO"
          className="font-mono"
          value={form.channelId ?? ""}
          onChange={(e) => onChange({ ...form, channelId: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">In Slack: right-click the channel → View channel details → Channel ID at the bottom.</p>
      </div>

      <div className="space-y-2">
        <Label>Channel Display Name</Label>
        <Input
          placeholder="e.g. #portfolio-updates"
          value={form.channelName ?? ""}
          onChange={(e) => onChange({ ...form, channelName: e.target.value })}
        />
      </div>

      <div className="space-y-2">
        <Label>Agent</Label>
        <Select
          value={form.agent ?? ""}
          onValueChange={(v) => onChange({ ...form, agent: v || "" })}
        >
          <SelectTrigger><SelectValue placeholder="Select agent…" /></SelectTrigger>
          <SelectContent>
            {AGENTS.map((a) => (
              <SelectItem key={a} value={a}>{a}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Scope</Label>
        <Select
          value={form.scope ?? ""}
          onValueChange={(v) => onChange({ ...form, scope: v || "" })}
        >
          <SelectTrigger><SelectValue placeholder="Select scope…" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="org">org — any authenticated user</SelectItem>
            <SelectItem value="team">team — team members only</SelectItem>
            <SelectItem value="admin">admin — admins only</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create dialog
// ---------------------------------------------------------------------------

function CreateDialog({
  open,
  onOpenChange,
  providers,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  providers: Provider[];
  onCreated: (b: Binding) => void;
}) {
  const [form, setForm] = useState<Partial<Binding>>({});
  const [saving, setSaving] = useState(false);

  async function handleCreate() {
    if (!form.channelId || !form.channelName || !form.providerId || !form.agent || !form.scope) return;
    setSaving(true);
    const res = await fetch("/api/channel-bindings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    const binding = await res.json();
    setSaving(false);
    onCreated(binding);
    onOpenChange(false);
    setForm({});
  }

  const valid = !!(form.channelId && form.channelName && form.providerId && form.agent && form.scope);

  return (
    <Dialog open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) setForm({}); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create Channel Binding</DialogTitle>
          <DialogDescription>Bind a chat channel to an AI agent.</DialogDescription>
        </DialogHeader>
        <BindingForm form={form} providers={providers} onChange={setForm} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleCreate} disabled={saving || !valid}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Create Binding
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog
// ---------------------------------------------------------------------------

function EditDialog({
  binding,
  providers,
  onOpenChange,
  onSaved,
}: {
  binding: Binding | null;
  providers: Provider[];
  onOpenChange: (v: boolean) => void;
  onSaved: (b: Binding) => void;
}) {
  const [form, setForm] = useState<Partial<Binding>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (binding) setForm({ ...binding });
  }, [binding]);

  async function handleSave() {
    if (!binding) return;
    setSaving(true);
    const res = await fetch(`/api/channel-bindings/${binding.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    const updated = await res.json();
    setSaving(false);
    onSaved(updated);
    onOpenChange(false);
  }

  if (!binding) return null;

  return (
    <Dialog open={!!binding} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit Channel Binding</DialogTitle>
          <DialogDescription>Update the agent or scope for this channel.</DialogDescription>
        </DialogHeader>
        <BindingForm form={form} providers={providers} onChange={setForm} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Save Changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChannelsPage() {
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingBinding, setEditingBinding] = useState<Binding | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/api/channel-bindings").then((r) => r.json()),
      fetch("/api/providers").then((r) => r.json()),
    ]).then(([b, p]) => {
      setBindings(b);
      setProviders(p);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  async function handleDelete(id: string) {
    await fetch(`/api/channel-bindings/${id}`, { method: "DELETE" });
    setBindings((prev) => prev.filter((b) => b.id !== id));
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
          <h1 className="text-2xl font-bold tracking-tight">Channel Binding</h1>
          <p className="text-muted-foreground">Map chat channels to AI agents. Admin-only configuration.</p>
        </div>
        <Button onClick={() => setCreateOpen(true)} disabled={providers.length === 0}>
          <Plus className="h-4 w-4 mr-2" /> New Binding
        </Button>
      </div>

      {providers.length === 0 && (
        <Card className="border-amber-500/20 bg-amber-500/5">
          <CardContent className="pt-4 text-sm text-amber-400">
            No chat providers configured yet. Add a provider in <strong>Chat Providers</strong> before creating channel bindings.
          </CardContent>
        </Card>
      )}

      {bindings.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-12 text-center gap-3">
            <Unplug className="h-8 w-8 text-muted-foreground" />
            <div>
              <p className="font-medium">No channel bindings yet</p>
              <p className="text-sm text-muted-foreground mt-1">Bind a Slack or Teams channel to an agent to start routing messages.</p>
            </div>
            {providers.length > 0 && (
              <Button onClick={() => setCreateOpen(true)} className="mt-2">
                <Plus className="h-4 w-4 mr-2" /> New Binding
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bindings.map((binding) => (
                  <TableRow key={binding.id}>
                    <TableCell>
                      <div>
                        <p className="font-medium">{binding.channelName}</p>
                        <p className="text-xs font-mono text-muted-foreground">{binding.channelId}</p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{binding.provider}</Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-sm">{binding.agent}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-xs border ${scopeColors[binding.scope] ?? ""}`}>
                        {binding.scope}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{binding.updatedAt}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setEditingBinding(binding)}>
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-destructive hover:bg-destructive/10"
                          onClick={() => handleDelete(binding.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
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

      <Card className="border-amber-500/20 bg-amber-500/5">
        <CardContent className="pt-4 text-xs text-muted-foreground space-y-1">
          <p>• All binding changes are recorded in the audit log with the admin's identity.</p>
          <p>• Regular users cannot view or modify channel bindings.</p>
          <p>• Compliance Agent bindings are restricted to admin-scoped channels only.</p>
        </CardContent>
      </Card>

      <CreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        providers={providers}
        onCreated={(b) => setBindings((prev) => [...prev, b])}
      />
      <EditDialog
        binding={editingBinding}
        providers={providers}
        onOpenChange={(v) => { if (!v) setEditingBinding(null); }}
        onSaved={(b) => setBindings((prev) => prev.map((x) => x.id === b.id ? b : x))}
      />
    </div>
  );
}
