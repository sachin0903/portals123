"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Search, Download, Eye, CheckCircle2, XCircle, Shield } from "lucide-react";
import { mockAuditLogs } from "@/lib/mock-data";

const outcomeColors: Record<string, string> = {
  success: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  denied: "bg-red-500/10 text-red-400 border-red-500/20",
};

export default function AuditPage() {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");

  const logs = mockAuditLogs.filter((log) => {
    const matchesSearch =
      !search ||
      log.user.toLowerCase().includes(search.toLowerCase()) ||
      log.agent.toLowerCase().includes(search.toLowerCase()) ||
      log.channel.toLowerCase().includes(search.toLowerCase());
    const matchesFilter = filter === "all" || log.outcome === filter;
    return matchesSearch && matchesFilter;
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Audit Logs</h1>
          <p className="text-muted-foreground">Immutable record of all agent invocations. Accessible to admins and compliance personnel only.</p>
        </div>
        <Button variant="outline"><Download className="h-4 w-4 mr-2" /> Export CSV</Button>
      </div>

      {/* Stats */}
      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">Total events today</p>
            <p className="text-2xl font-bold mt-1">{mockAuditLogs.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">Successful</p>
            <p className="text-2xl font-bold mt-1 text-emerald-500">
              {mockAuditLogs.filter(l => l.outcome === "success").length}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">Denied</p>
            <p className="text-2xl font-bold mt-1 text-red-500">
              {mockAuditLogs.filter(l => l.outcome === "denied").length}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">Total tokens consumed</p>
            <p className="text-2xl font-bold mt-1">
              {(mockAuditLogs.reduce((s, l) => s + l.tokens, 0) / 1000).toFixed(0)}K
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by user, agent, or channel…"
            className="pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select value={filter} onValueChange={(v) => setFilter(v ?? "all")}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All outcomes</SelectItem>
            <SelectItem value="success">Success only</SelectItem>
            <SelectItem value="denied">Denied only</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Tokens</TableHead>
                <TableHead>Latency</TableHead>
                <TableHead className="text-right">Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.map((log) => (
                <TableRow key={log.id} className={log.outcome === "denied" ? "bg-red-500/5" : ""}>
                  <TableCell className="font-mono text-xs text-muted-foreground whitespace-nowrap">{log.timestamp}</TableCell>
                  <TableCell className="text-sm">{log.user}</TableCell>
                  <TableCell className="text-sm">{log.agent}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{log.channel}</TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="text-xs">{log.provider}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={`text-xs border ${outcomeColors[log.outcome]}`}>
                      {log.outcome === "success"
                        ? <CheckCircle2 className="h-3 w-3 mr-1 inline" />
                        : <XCircle className="h-3 w-3 mr-1 inline" />}
                      {log.outcome}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {log.tokens > 0 ? log.tokens.toLocaleString() : "—"}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {log.latencyMs > 0 ? `${log.latencyMs}ms` : "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Dialog>
                      <DialogTrigger render={<Button variant="ghost" size="icon" className="h-8 w-8"><Eye className="h-3.5 w-3.5" /></Button>} />
                      <DialogContent>
                        <DialogHeader>
                          <DialogTitle>Event {log.id}</DialogTitle>
                        </DialogHeader>
                        <div className="space-y-3 text-sm">
                          {Object.entries(log).map(([k, v]) => (
                            <div key={k} className="flex justify-between">
                              <span className="text-muted-foreground capitalize">{k}</span>
                              <span className="font-mono">{String(v)}</span>
                            </div>
                          ))}
                        </div>
                      </DialogContent>
                    </Dialog>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card className="border-amber-500/20 bg-amber-500/5">
        <CardContent className="pt-4 text-xs text-muted-foreground space-y-1">
          <p>• Audit records are append-only — no update or delete operations are possible via the API.</p>
          <p>• Each record includes a cryptographic hash to detect tampering.</p>
          <p>• The Audit DB is hosted in a separate cloud account from operational data.</p>
          <p>• Retention: 5 years minimum (MiFID II compliance).</p>
        </CardContent>
      </Card>
    </div>
  );
}
