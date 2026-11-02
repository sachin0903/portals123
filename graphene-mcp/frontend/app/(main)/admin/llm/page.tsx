"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Cpu, CheckCircle2, Key, AlertTriangle, Loader2 } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LLMProvider {
  id: string;
  name: string;
  type: string;
  models: string[];
  activeModel: string;
  active: boolean;
  keyConfigured: boolean;
  dataResidency: string;
  usageToday: number;
  costToday: number;
}

// ---------------------------------------------------------------------------
// Key dialog
// ---------------------------------------------------------------------------

function KeyDialog({
  provider,
  onOpenChange,
  onSaved,
}: {
  provider: LLMProvider | null;
  onOpenChange: (v: boolean) => void;
  onSaved: (p: LLMProvider) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (provider) {
      setApiKey("");
      setModel(provider.activeModel);
      setError("");
    }
  }, [provider]);

  async function handleSave() {
    if (!provider) return;
    setSaving(true);
    setError("");

    const res = await fetch(`/api/llm-providers/${provider.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apiKey, activeModel: model }),
    });

    const data = await res.json();
    setSaving(false);

    if (!res.ok) { setError(data.error); return; }
    onSaved(data);
    onOpenChange(false);
  }

  if (!provider) return null;

  return (
    <Dialog open={!!provider} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{provider.keyConfigured ? "Rotate" : "Configure"} API Key — {provider.name}</DialogTitle>
          <DialogDescription>
            The key is stored in the data file and read at runtime by the agent backend.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>API Key</Label>
            <Input
              type="password"
              placeholder={provider.keyConfigured ? "Leave blank to keep existing key" : "sk-ant-… or sk-…"}
              className="font-mono"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Default Model</Label>
            <Select value={model} onValueChange={(v) => setModel(v || provider.activeModel)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {provider.models.map((m) => (
                  <SelectItem key={m} value={m}>{m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving || (!apiKey && !provider.keyConfigured)}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function LLMPage() {
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingProvider, setEditingProvider] = useState<LLMProvider | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [activateError, setActivateError] = useState<string>("");

  useEffect(() => {
    fetch("/api/llm-providers")
      .then((r) => r.json())
      .then((data) => { setProviders(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  async function handleActivate(id: string) {
    setActivating(id);
    setActivateError("");
    const res = await fetch(`/api/llm-providers/${id}/activate`, { method: "POST" });
    const data = await res.json();
    setActivating(null);
    if (!res.ok) { setActivateError(data.error); return; }
    setProviders((prev) => prev.map((p) => ({ ...p, active: p.id === id })));
  }

  function handleSaved(updated: LLMProvider) {
    setProviders((prev) => prev.map((p) => (p.id === updated.id ? { ...p, ...updated } : p)));
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
      <div>
        <h1 className="text-2xl font-bold tracking-tight">LLM Providers</h1>
        <p className="text-muted-foreground">Configure language model providers for agent inference.</p>
      </div>

      {activateError && (
        <p className="text-sm text-destructive">{activateError}</p>
      )}

      <div className="grid gap-4 grid-cols-1 lg:grid-cols-3">
        {providers.map((provider) => (
          <Card key={provider.id} className={provider.active ? "border-primary/50 ring-1 ring-primary/20" : ""}>
            <CardHeader>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
                    <Cpu className="h-5 w-5 text-indigo-400" />
                  </div>
                  <div>
                    <CardTitle className="text-base">{provider.name}</CardTitle>
                    <CardDescription className="text-xs">{provider.dataResidency}</CardDescription>
                  </div>
                </div>
                {provider.active && (
                  <Badge className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs">
                    <CheckCircle2 className="h-3 w-3 mr-1" /> Active
                  </Badge>
                )}
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              <Separator />

              <div>
                <p className="text-xs text-muted-foreground mb-2">Models</p>
                <div className="flex flex-wrap gap-1.5">
                  {provider.models.map((m) => (
                    <Badge
                      key={m}
                      variant={m === provider.activeModel ? "default" : "secondary"}
                      className="text-xs font-mono"
                    >
                      {m}
                    </Badge>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Key className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-sm">API Key</span>
                </div>
                <Badge
                  variant={provider.keyConfigured ? "outline" : "destructive"}
                  className="text-xs"
                >
                  {provider.keyConfigured ? "Configured" : "Not configured"}
                </Badge>
              </div>

              {provider.active && (
                <>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Tokens today</span>
                    <span className="font-semibold">{(provider.usageToday / 1_000_000).toFixed(2)}M</span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Cost today</span>
                    <span className="font-semibold">${provider.costToday.toFixed(2)}</span>
                  </div>
                </>
              )}

              <div className="flex gap-2 pt-1">
                {!provider.active && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    disabled={activating === provider.id || !provider.keyConfigured}
                    onClick={() => handleActivate(provider.id)}
                    title={!provider.keyConfigured ? "Configure an API key first" : ""}
                  >
                    {activating === provider.id && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
                    Set Active
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  className={provider.active ? "flex-1" : ""}
                  onClick={() => setEditingProvider(provider)}
                >
                  <Key className="h-3.5 w-3.5 mr-1.5" />
                  {provider.keyConfigured ? "Rotate Key" : "Add Key"}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="border-blue-500/20 bg-blue-500/5">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-blue-400 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" /> Regulated Environment Recommendation
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground space-y-1">
          <p>• For MiFID II and SEC-regulated deployments, use <strong className="text-foreground">AWS Bedrock</strong> to keep all data within your cloud boundary.</p>
          <p>• OpenAI and Anthropic (SaaS) process data outside your boundary — ensure a valid Data Processing Agreement (DPA) is in place.</p>
          <p>• LLM invocations are logged (model, token count, latency) — prompt and completion content is <strong className="text-foreground">never logged</strong> in production.</p>
        </CardContent>
      </Card>

      <KeyDialog
        provider={editingProvider}
        onOpenChange={(v) => { if (!v) setEditingProvider(null); }}
        onSaved={handleSaved}
      />
    </div>
  );
}
