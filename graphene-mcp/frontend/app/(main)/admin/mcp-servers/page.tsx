"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Plus, Server, CheckCircle2, Wrench, RefreshCw, Trash2, AlertCircle, Loader2, ToggleLeft, ToggleRight } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface McpTool {
  name: string;
  description: string;
  enabled: boolean;
}

interface McpServer {
  id: string;
  name: string;
  endpoint: string;
  description: string;
  authToken: string;
  status: "healthy" | "degraded" | "offline" | "unknown";
  tools: McpTool[];
  latencyMs: number;
  callsToday: number;
  agentsUsing: string[];
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusBadge(status: McpServer["status"]) {
  if (status === "healthy")
    return (
      <Badge className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs">
        <CheckCircle2 className="h-3 w-3 mr-1" /> healthy
      </Badge>
    );
  if (status === "degraded")
    return (
      <Badge className="bg-amber-500/10 text-amber-400 border border-amber-500/20 text-xs">
        <AlertCircle className="h-3 w-3 mr-1" /> degraded
      </Badge>
    );
  if (status === "offline")
    return (
      <Badge className="bg-red-500/10 text-red-400 border border-red-500/20 text-xs">
        <AlertCircle className="h-3 w-3 mr-1" /> offline
      </Badge>
    );
  return (
    <Badge variant="secondary" className="text-xs">unknown</Badge>
  );
}

// ---------------------------------------------------------------------------
// Register dialog (new server)
// ---------------------------------------------------------------------------

function RegisterDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (s: McpServer) => void;
}) {
  const [form, setForm] = useState({ name: "", endpoint: "", description: "", authToken: "" });
  const [saving, setSaving] = useState(false);

  async function handleCreate() {
    if (!form.name || !form.endpoint) return;
    setSaving(true);
    const res = await fetch("/api/mcp-servers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    const server = await res.json();
    setSaving(false);
    onCreated(server);
    onOpenChange(false);
    setForm({ name: "", endpoint: "", description: "", authToken: "" });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Register MCP Server</DialogTitle>
          <DialogDescription>Add a new MCP server endpoint to the platform.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>Server Name</Label>
            <Input placeholder="e.g. Portfolio MCP" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Endpoint URL</Label>
            <Input placeholder="http://my-mcp-server:8001/mcp" className="font-mono" value={form.endpoint} onChange={(e) => setForm({ ...form, endpoint: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Description</Label>
            <Input placeholder="What data does this server expose?" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Auth Token (optional)</Label>
            <Input type="password" placeholder="Bearer token for mTLS or service auth" value={form.authToken} onChange={(e) => setForm({ ...form, authToken: e.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleCreate} disabled={saving || !form.name || !form.endpoint}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Register
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog (existing server)
// ---------------------------------------------------------------------------

function EditDialog({
  server,
  onOpenChange,
  onSaved,
}: {
  server: McpServer | null;
  onOpenChange: (v: boolean) => void;
  onSaved: (s: McpServer) => void;
}) {
  const [form, setForm] = useState({ name: "", endpoint: "", description: "", authToken: "" });
  const [tools, setTools] = useState<McpTool[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (server) {
      setForm({ name: server.name, endpoint: server.endpoint, description: server.description, authToken: server.authToken });
      setTools(server.tools ?? []);
      setDiscoverError("");
    }
  }, [server]);

  async function handleDiscover() {
    if (!server) return;
    setDiscovering(true);
    setDiscoverError("");
    const res = await fetch(`/api/mcp-servers/${server.id}/discover`, { method: "POST" });
    const data = await res.json();
    setDiscovering(false);
    if (!res.ok) {
      setDiscoverError(data.error ?? "Discovery failed");
      return;
    }
    // Merge discovered tools — preserve existing enabled state, add new tools as enabled
    const discovered: Array<{ name: string; description: string }> = data.tools ?? [];
    const existingMap = new Map(tools.map((t) => [t.name, t]));
    setTools(
      discovered.map((d) => ({
        name: d.name,
        description: d.description,
        enabled: existingMap.get(d.name)?.enabled ?? true,
      }))
    );
  }

  function toggleTool(name: string) {
    setTools((prev) => prev.map((t) => (t.name === name ? { ...t, enabled: !t.enabled } : t)));
  }

  async function handleSave() {
    if (!server) return;
    setSaving(true);
    const res = await fetch(`/api/mcp-servers/${server.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, tools }),
    });
    const updated = await res.json();
    setSaving(false);
    onSaved(updated);
    onOpenChange(false);
  }

  if (!server) return null;

  return (
    <Dialog open={!!server} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit MCP Server</DialogTitle>
          <DialogDescription>Update server details and manage which tools are enabled.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Basic fields */}
          <div className="space-y-2">
            <Label>Server Name</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Endpoint URL</Label>
            <Input className="font-mono" value={form.endpoint} onChange={(e) => setForm({ ...form, endpoint: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Description</Label>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Auth Token</Label>
            <Input type="password" value={form.authToken} onChange={(e) => setForm({ ...form, authToken: e.target.value })} />
          </div>

          <Separator />

          {/* Tool assignment */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>Assigned Tools</Label>
              <Button variant="outline" size="sm" onClick={handleDiscover} disabled={discovering}>
                {discovering ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5 mr-1.5" />}
                Discover from server
              </Button>
            </div>

            {discoverError && (
              <p className="text-xs text-destructive">{discoverError}</p>
            )}

            {tools.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">No tools yet — click "Discover from server" to fetch the tool list.</p>
            ) : (
              <div className="space-y-2">
                {tools.map((tool) => (
                  <div key={tool.name} className="flex items-start justify-between gap-3 rounded-md border p-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-mono font-medium">{tool.name}</p>
                      <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5">{tool.description}</p>
                    </div>
                    <button
                      onClick={() => toggleTool(tool.name)}
                      className="shrink-0 mt-0.5 text-muted-foreground hover:text-foreground transition-colors"
                      title={tool.enabled ? "Disable tool" : "Enable tool"}
                    >
                      {tool.enabled
                        ? <ToggleRight className="h-6 w-6 text-emerald-500" />
                        : <ToggleLeft className="h-6 w-6" />}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
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

export default function McpServersPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServer | null>(null);

  useEffect(() => {
    fetch("/api/mcp-servers")
      .then((r) => r.json())
      .then((data) => { setServers(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  function handleCreated(server: McpServer) {
    setServers((prev) => [...prev, server]);
  }

  function handleSaved(updated: McpServer) {
    setServers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }

  async function handleDelete(id: string) {
    await fetch(`/api/mcp-servers/${id}`, { method: "DELETE" });
    setServers((prev) => prev.filter((s) => s.id !== id));
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
          <h1 className="text-2xl font-bold tracking-tight">MCP Servers</h1>
          <p className="text-muted-foreground">Manage Model Context Protocol servers that provide data tools to agents.</p>
        </div>
        <Button onClick={() => setRegisterOpen(true)}>
          <Plus className="h-4 w-4 mr-2" /> Register Server
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        {servers.map((srv) => (
          <Card key={srv.id}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <Server className="h-4 w-4 text-muted-foreground" />
                <div className={`h-2 w-2 rounded-full ${srv.status === "healthy" ? "bg-emerald-500" : srv.status === "degraded" ? "bg-amber-500" : "bg-red-500"}`} />
              </div>
              <CardTitle className="text-sm">{srv.name}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Latency</span>
                <span className="font-medium">{srv.latencyMs}ms</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Calls today</span>
                <span className="font-medium">{srv.callsToday.toLocaleString()}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Tools</span>
                <span className="font-medium">{srv.tools.filter((t) => t.enabled).length} / {srv.tools.length}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Detail table */}
      <Card>
        <CardHeader>
          <CardTitle>Server Details</CardTitle>
          <CardDescription>Tool capabilities and agent access for each MCP server</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Server</TableHead>
                <TableHead>Endpoint</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Tools</TableHead>
                <TableHead>Used by agents</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {servers.map((srv) => (
                <TableRow key={srv.id}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Server className="h-4 w-4 text-muted-foreground" />
                      <div>
                        <p className="font-medium text-sm">{srv.name}</p>
                        {srv.description && <p className="text-xs text-muted-foreground">{srv.description}</p>}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-xs text-muted-foreground">{srv.endpoint}</span>
                  </TableCell>
                  <TableCell>{statusBadge(srv.status)}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {srv.tools.filter((t) => t.enabled).map((t) => (
                        <Badge key={t.name} variant="secondary" className="text-xs font-mono">{t.name}</Badge>
                      ))}
                      {srv.tools.some((t) => !t.enabled) && (
                        <Badge variant="outline" className="text-xs text-muted-foreground">
                          +{srv.tools.filter((t) => !t.enabled).length} disabled
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {srv.agentsUsing.map((a) => (
                        <Badge key={a} variant="outline" className="text-xs">{a}</Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" onClick={() => setEditingServer(srv)}>
                        <Wrench className="h-3.5 w-3.5 mr-1" /> Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive hover:bg-destructive/10"
                        onClick={() => handleDelete(srv.id)}
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

      <Card className="border-amber-500/20 bg-amber-500/5">
        <CardContent className="pt-4 text-xs text-muted-foreground space-y-1">
          <p>• All MCP server communications use mTLS for east-west traffic encryption.</p>
          <p>• Agents cannot access data stores directly — all data access must go through a registered MCP server.</p>
          <p>• Disable individual tools to restrict what data an agent can access without removing the entire server.</p>
        </CardContent>
      </Card>

      <RegisterDialog open={registerOpen} onOpenChange={setRegisterOpen} onCreated={handleCreated} />
      <EditDialog server={editingServer} onOpenChange={(v) => { if (!v) setEditingServer(null); }} onSaved={handleSaved} />
    </div>
  );
}
