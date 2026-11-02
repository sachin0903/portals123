"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Bot, Settings, Activity, Clock, Zap } from "lucide-react";
import { mockAgents } from "@/lib/mock-data";

const scopeColors: Record<string, string> = {
  org: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  team: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  admin: "bg-red-500/10 text-red-400 border-red-500/20",
};

export default function AgentsPage() {
  const [agents, setAgents] = useState(mockAgents);
  const [editingAgent, setEditingAgent] = useState<typeof mockAgents[0] | null>(null);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Agent Configuration</h1>
        <p className="text-muted-foreground">Configure AI agents, their scopes, MCP server access, and token budgets.</p>
      </div>

      <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
        {agents.map((agent) => (
          <Card key={agent.id}>
            <CardHeader>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
                    <Bot className="h-5 w-5 text-indigo-400" />
                  </div>
                  <div>
                    <CardTitle className="text-base">{agent.name}</CardTitle>
                    <CardDescription className="text-xs">{agent.model}</CardDescription>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className={`text-xs border ${scopeColors[agent.scope]}`}>
                    {agent.scope}
                  </Badge>
                  <div className="h-2 w-2 rounded-full bg-emerald-500" />
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              <Separator />

              {/* Stats */}
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground mb-1">
                    <Activity className="h-3 w-3" /> Calls today
                  </div>
                  <p className="font-bold text-sm">{agent.invocationsToday}</p>
                </div>
                <div>
                  <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground mb-1">
                    <Clock className="h-3 w-3" /> Avg latency
                  </div>
                  <p className="font-bold text-sm">{(agent.avgLatencyMs / 1000).toFixed(1)}s</p>
                </div>
                <div>
                  <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground mb-1">
                    <Zap className="h-3 w-3" /> Token budget
                  </div>
                  <p className="font-bold text-sm">{(agent.tokenBudget / 1000).toFixed(0)}K</p>
                </div>
              </div>

              {/* Token budget usage (mock 40-70%) */}
              <div>
                <div className="flex justify-between text-xs text-muted-foreground mb-1">
                  <span>Token usage today</span>
                  <span>42% of budget</span>
                </div>
                <Progress value={42} className="h-1.5" />
              </div>

              {/* MCP Servers */}
              <div>
                <p className="text-xs text-muted-foreground mb-2">MCP Servers</p>
                <div className="flex flex-wrap gap-1.5">
                  {agent.mcpServers.map((srv) => (
                    <Badge key={srv} variant="secondary" className="text-xs">{srv}</Badge>
                  ))}
                </div>
              </div>

              {/* System prompt preview */}
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">System Prompt (truncated)</p>
                <p className="text-xs text-muted-foreground bg-muted rounded-md p-2 italic line-clamp-2">
                  {agent.systemPromptPreview}
                </p>
              </div>

              <Dialog>
                <DialogTrigger render={<Button variant="outline" size="sm" className="w-full" onClick={() => setEditingAgent(agent)}><Settings className="h-3.5 w-3.5 mr-1.5" /> Configure Agent</Button>} />
                <DialogContent className="sm:max-w-lg">
                  <DialogHeader>
                    <DialogTitle>Configure {agent.name}</DialogTitle>
                    <DialogDescription>Adjust model, token budget, and MCP server access.</DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 py-2">
                    <div className="space-y-2">
                      <Label>Model</Label>
                      <Select defaultValue={agent.model}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="claude-opus-4-8">claude-opus-4-8 (highest capability)</SelectItem>
                          <SelectItem value="claude-sonnet-4-6">claude-sonnet-4-6 (balanced)</SelectItem>
                          <SelectItem value="claude-haiku-4-5">claude-haiku-4-5 (fastest)</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label>Token Budget per Invocation</Label>
                      <Input type="number" defaultValue={agent.tokenBudget} />
                      <p className="text-xs text-muted-foreground">Prevents runaway LLM costs from adversarial long-context attacks.</p>
                    </div>
                    <div className="space-y-2">
                      <Label>System Prompt</Label>
                      <textarea
                        className="w-full min-h-[120px] rounded-md border bg-background px-3 py-2 text-sm resize-y font-mono"
                        defaultValue={agent.systemPromptPreview}
                      />
                      <p className="text-xs text-muted-foreground">System prompts are never exposed to end users.</p>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline">Cancel</Button>
                    <Button>Save Changes</Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
