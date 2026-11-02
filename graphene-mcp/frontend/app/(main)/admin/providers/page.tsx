"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Plus, Hash, Globe, Settings, Trash2, CheckCircle2, MessageSquare, Loader2, WifiOff, FlaskConical, XCircle } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Provider {
  id: string;
  name: string;
  type: "slack" | "teams" | "telegram" | "web";
  mode: string;
  status: "connected" | "disconnected";
  channels: number;
  lastEvent: string;
  workspaceId: string;
  botToken: string;
  appToken: string;
  signingSecret: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const providerIcons: Record<string, React.ReactNode> = {
  slack:    <Hash className="h-5 w-5 text-purple-400" />,
  teams:    <MessageSquare className="h-5 w-5 text-blue-400" />,
  telegram: <MessageSquare className="h-5 w-5 text-sky-400" />,
  web:      <Globe className="h-5 w-5 text-emerald-400" />,
};

const providerLabels: Record<string, string> = {
  slack:    "Slack",
  teams:    "Microsoft Teams",
  telegram: "Telegram",
  web:      "Web UI",
};

// ---------------------------------------------------------------------------
// Add Provider dialog
// ---------------------------------------------------------------------------

function AddProviderDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (p: Provider) => void;
}) {
  const [type, setType] = useState<string>("");
  const [form, setForm] = useState({
    name: "",
    workspaceId: "",
    botToken: "",
    appToken: "",
    signingSecret: "",
  });
  const [saving, setSaving] = useState(false);

  function reset() {
    setType("");
    setForm({ name: "", workspaceId: "", botToken: "", appToken: "", signingSecret: "" });
  }

  async function handleCreate() {
    if (!type || !form.name) return;
    setSaving(true);
    const res = await fetch("/api/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, type }),
    });
    const provider = await res.json();
    setSaving(false);
    onCreated(provider);
    onOpenChange(false);
    reset();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) reset(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add Chat Provider</DialogTitle>
          <DialogDescription>Connect a chat platform to Graphene.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>Provider Type</Label>
            <Select value={type} onValueChange={(v) => setType(v ?? "")}>
              <SelectTrigger><SelectValue placeholder="Select provider…" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="slack">Slack</SelectItem>
                <SelectItem value="teams">Microsoft Teams</SelectItem>
                <SelectItem value="telegram">Telegram</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Display Name</Label>
            <Input
              placeholder="e.g. Acme Corp Slack"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>

          {type === "slack" && (
            <>
              <div className="space-y-2">
                <Label>Workspace ID</Label>
                <Input
                  placeholder="T04ABCDEF12"
                  className="font-mono"
                  value={form.workspaceId}
                  onChange={(e) => setForm({ ...form, workspaceId: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">Found in your Slack workspace URL or desktop app → right-click workspace name.</p>
              </div>

              <div className="space-y-2">
                <Label>Bot Token</Label>
                <Input
                  type="password"
                  placeholder="xoxb-…"
                  className="font-mono"
                  value={form.botToken}
                  onChange={(e) => setForm({ ...form, botToken: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">OAuth & Permissions → OAuth Tokens for Your Workspace.</p>
              </div>

              <div className="space-y-2">
                <Label>App Token</Label>
                <Input
                  type="password"
                  placeholder="xapp-…"
                  className="font-mono"
                  value={form.appToken}
                  onChange={(e) => setForm({ ...form, appToken: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">Basic Information → App-Level Tokens. Generate with <code className="text-xs bg-muted px-1 rounded">connections:write</code> scope.</p>
              </div>

              <div className="space-y-2">
                <Label>Signing Secret</Label>
                <Input
                  type="password"
                  placeholder="8f742231b10e…"
                  className="font-mono"
                  value={form.signingSecret}
                  onChange={(e) => setForm({ ...form, signingSecret: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">Basic Information → App Credentials → Signing Secret.</p>
              </div>
            </>
          )}

          {type === "teams" && (
            <>
              <div className="space-y-2">
                <Label>Bot Token</Label>
                <Input
                  type="password"
                  placeholder="Bot Framework App ID"
                  value={form.botToken}
                  onChange={(e) => setForm({ ...form, botToken: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>Tenant ID</Label>
                <Input
                  placeholder="72f988bf-86f1-41af-91ab-2d7cd011db47"
                  className="font-mono"
                  value={form.workspaceId}
                  onChange={(e) => setForm({ ...form, workspaceId: e.target.value })}
                />
              </div>
            </>
          )}

          {type === "telegram" && (
            <div className="space-y-2">
              <Label>Bot Token</Label>
              <Input
                type="password"
                placeholder="110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
                className="font-mono"
                value={form.botToken}
                onChange={(e) => setForm({ ...form, botToken: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">Get from @BotFather on Telegram.</p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => { onOpenChange(false); reset(); }}>Cancel</Button>
          <Button onClick={handleCreate} disabled={saving || !type || !form.name}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Connect Provider
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
  provider,
  onOpenChange,
  onSaved,
}: {
  provider: Provider | null;
  onOpenChange: (v: boolean) => void;
  onSaved: (p: Provider) => void;
}) {
  const [form, setForm] = useState({ name: "", workspaceId: "", botToken: "", appToken: "", signingSecret: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (provider) {
      setForm({
        name: provider.name,
        workspaceId: provider.workspaceId,
        botToken: provider.botToken,
        appToken: provider.appToken,
        signingSecret: provider.signingSecret,
      });
    }
  }, [provider]);

  async function handleSave() {
    if (!provider) return;
    setSaving(true);
    const res = await fetch(`/api/providers/${provider.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    const updated = await res.json();
    setSaving(false);
    onSaved(updated);
    onOpenChange(false);
  }

  if (!provider) return null;

  return (
    <Dialog open={!!provider} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit {providerLabels[provider.type]} Provider</DialogTitle>
          <DialogDescription>Update credentials — new values are stored securely.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>Display Name</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>

          {provider.type === "slack" && (
            <>
              <div className="space-y-2">
                <Label>Workspace ID</Label>
                <Input className="font-mono" value={form.workspaceId} onChange={(e) => setForm({ ...form, workspaceId: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>Bot Token</Label>
                <Input type="password" placeholder="xoxb-… (leave blank to keep current)" className="font-mono" value={form.botToken} onChange={(e) => setForm({ ...form, botToken: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>App Token</Label>
                <Input type="password" placeholder="xapp-… (leave blank to keep current)" className="font-mono" value={form.appToken} onChange={(e) => setForm({ ...form, appToken: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>Signing Secret</Label>
                <Input type="password" placeholder="leave blank to keep current" className="font-mono" value={form.signingSecret} onChange={(e) => setForm({ ...form, signingSecret: e.target.value })} />
              </div>
            </>
          )}

          {provider.type === "teams" && (
            <>
              <div className="space-y-2">
                <Label>Bot Token</Label>
                <Input type="password" value={form.botToken} onChange={(e) => setForm({ ...form, botToken: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>Tenant ID</Label>
                <Input className="font-mono" value={form.workspaceId} onChange={(e) => setForm({ ...form, workspaceId: e.target.value })} />
              </div>
            </>
          )}

          {provider.type === "telegram" && (
            <div className="space-y-2">
              <Label>Bot Token</Label>
              <Input type="password" value={form.botToken} onChange={(e) => setForm({ ...form, botToken: e.target.value })} />
            </div>
          )}
        </div>

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

interface TestResult {
  ok: boolean;
  workspace?: string;
  workspaceId?: string;
  botUser?: string;
  error?: string;
}

export default function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  useEffect(() => {
    fetch("/api/providers")
      .then((r) => r.json())
      .then((data) => { setProviders(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  async function handleDelete(id: string) {
    await fetch(`/api/providers/${id}`, { method: "DELETE" });
    setProviders((prev) => prev.filter((p) => p.id !== id));
    setTestResults((prev) => { const next = { ...prev }; delete next[id]; return next; });
  }

  async function handleTest(id: string) {
    setTesting(id);
    setTestResults((prev) => { const next = { ...prev }; delete next[id]; return next; });
    const res = await fetch(`/api/providers/${id}/test`, { method: "POST" });
    const data: TestResult = await res.json();
    setTesting(null);
    setTestResults((prev) => ({ ...prev, [id]: data }));
    // If test succeeded, refresh providers to pick up updated status + workspaceId
    if (data.ok) {
      const updated = await fetch("/api/providers").then((r) => r.json());
      setProviders(updated);
    }
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
          <h1 className="text-2xl font-bold tracking-tight">Chat Providers</h1>
          <p className="text-muted-foreground">Configure and manage chat platform integrations.</p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4 mr-2" /> Add Provider
        </Button>
      </div>

      {providers.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-12 text-center gap-3">
            <WifiOff className="h-8 w-8 text-muted-foreground" />
            <div>
              <p className="font-medium">No providers connected</p>
              <p className="text-sm text-muted-foreground mt-1">Add a Slack, Teams, or Telegram integration to get started.</p>
            </div>
            <Button onClick={() => setAddOpen(true)} className="mt-2">
              <Plus className="h-4 w-4 mr-2" /> Add Provider
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
          {providers.map((provider) => (
            <Card key={provider.id}>
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
                      {providerIcons[provider.type]}
                    </div>
                    <div>
                      <CardTitle className="text-base">{provider.name}</CardTitle>
                      <CardDescription className="text-xs mt-0.5">
                        {providerLabels[provider.type]} · {provider.mode}
                      </CardDescription>
                    </div>
                  </div>
                  <Badge className={provider.status === "connected"
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                    : "bg-zinc-500/10 text-zinc-400 border border-zinc-500/20"}>
                    <CheckCircle2 className="h-3 w-3 mr-1" />
                    {provider.status}
                  </Badge>
                </div>
              </CardHeader>

              <CardContent className="space-y-3">
                <Separator />
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <p className="text-muted-foreground text-xs">Workspace / Tenant ID</p>
                    <p className="font-mono text-xs truncate">{provider.workspaceId || "—"}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground text-xs">Channels bound</p>
                    <p className="font-semibold">{provider.channels}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground text-xs">Last event</p>
                    <p>{provider.lastEvent}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground text-xs">Bot Token</p>
                    <p className="font-mono text-xs text-muted-foreground">
                      {provider.botToken ? "••••••••" : "not set"}
                    </p>
                  </div>
                </div>

                {/* Test result */}
                {testResults[provider.id] && (
                  <div className={`rounded-md px-3 py-2 text-xs space-y-0.5 ${testResults[provider.id].ok ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
                    {testResults[provider.id].ok ? (
                      <>
                        <p className="font-medium flex items-center gap-1.5"><CheckCircle2 className="h-3.5 w-3.5" /> Connected successfully</p>
                        <p>Workspace: <span className="font-mono">{testResults[provider.id].workspace}</span> ({testResults[provider.id].workspaceId})</p>
                        <p>Bot user: <span className="font-mono">{testResults[provider.id].botUser}</span></p>
                      </>
                    ) : (
                      <p className="flex items-center gap-1.5"><XCircle className="h-3.5 w-3.5" /> {testResults[provider.id].error}</p>
                    )}
                  </div>
                )}

                <div className="flex gap-2 pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleTest(provider.id)}
                    disabled={testing === provider.id}
                  >
                    {testing === provider.id
                      ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                      : <FlaskConical className="h-3.5 w-3.5 mr-1.5" />}
                    Test
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={() => setEditingProvider(provider)}
                  >
                    <Settings className="h-3.5 w-3.5 mr-1.5" /> Configure
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive hover:bg-destructive/10"
                    onClick={() => handleDelete(provider.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card className="border-amber-500/20 bg-amber-500/5">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-amber-400">Security Notes</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground space-y-1">
          <p>• Signing secrets are used to verify all incoming Slack events via HMAC-SHA256.</p>
          <p>• App Tokens (<code className="bg-muted px-1 rounded">xapp-…</code>) require the <code className="bg-muted px-1 rounded">connections:write</code> scope for Socket Mode.</p>
          <p>• Bot Tokens (<code className="bg-muted px-1 rounded">xoxb-…</code>) require <code className="bg-muted px-1 rounded">chat:write</code> and <code className="bg-muted px-1 rounded">app_mentions:read</code> scopes at minimum.</p>
          <p>• Rotate credentials immediately if any exposure is suspected.</p>
        </CardContent>
      </Card>

      <AddProviderDialog open={addOpen} onOpenChange={setAddOpen} onCreated={(p) => setProviders((prev) => [...prev, p])} />
      <EditDialog provider={editingProvider} onOpenChange={(v) => { if (!v) setEditingProvider(null); }} onSaved={(p) => setProviders((prev) => prev.map((x) => x.id === p.id ? p : x))} />
    </div>
  );
}
