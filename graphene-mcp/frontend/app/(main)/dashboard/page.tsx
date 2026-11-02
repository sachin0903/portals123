"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { TrendingUp, TrendingDown, Activity, Bot, Server, Users, AlertTriangle } from "lucide-react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import {
  mockPortfolioStats, mockPnLHistory, mockAgentActivity,
  mockMcpServers, mockUsers, mockAgents,
} from "@/lib/mock-data";

function fmt(n: number) {
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toLocaleString()}`;
}

export default function DashboardPage() {
  const stats = mockPortfolioStats;
  const isUp = stats.dailyPnL > 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground">Platform overview — 8 Jun 2026</p>
      </div>

      {/* KPI row */}
      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Total AUM</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{fmt(stats.totalAUM)}</div>
            <p className="text-xs text-muted-foreground mt-1">Across all portfolios</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Daily P&L</CardTitle>
            {isUp ? <TrendingUp className="h-4 w-4 text-emerald-500" /> : <TrendingDown className="h-4 w-4 text-red-500" />}
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold ${isUp ? "text-emerald-500" : "text-red-500"}`}>
              {isUp ? "+" : ""}{fmt(stats.dailyPnL)}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {isUp ? "+" : ""}{stats.dailyPnLPct}% today
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Active Positions</CardTitle>
            <Bot className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.activePositions}</div>
            <p className="text-xs text-muted-foreground mt-1">Across all teams</p>
          </CardContent>
        </Card>

        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Alerts</CardTitle>
            <AlertTriangle className="h-4 w-4 text-amber-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-500">{stats.alerts}</div>
            <p className="text-xs text-muted-foreground mt-1">Require attention</p>
          </CardContent>
        </Card>
      </div>

      {/* Charts row */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Portfolio Value (6 months)</CardTitle>
            <CardDescription>Total AUM across all teams</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={mockPnLHistory}>
                <defs>
                  <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                <XAxis dataKey="date" tick={{ fontSize: 12, fill: "#888" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "#888" }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${(v / 1_000_000).toFixed(0)}M`} />
                <Tooltip formatter={(v) => fmt(Number(v))} contentStyle={{ background: "#1c1c1c", border: "1px solid #333", borderRadius: 8 }} />
                <Area type="monotone" dataKey="value" stroke="#6366f1" strokeWidth={2} fill="url(#colorValue)" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Agent Invocations Today</CardTitle>
            <CardDescription>Calls by agent type</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={mockAgentActivity}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#888" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "#888" }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "#1c1c1c", border: "1px solid #333", borderRadius: 8 }} />
                <Bar dataKey="calls" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* Status row */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Agent Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {mockAgents.map((agent) => (
              <div key={agent.id} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-emerald-500" />
                  <span className="text-sm">{agent.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{agent.invocationsToday} calls</span>
                  <Badge variant="outline" className="text-xs">
                    {agent.scope}
                  </Badge>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">MCP Server Health</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {mockMcpServers.map((srv) => (
              <div key={srv.id} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-emerald-500" />
                  <span className="text-sm">{srv.name}</span>
                </div>
                <span className="text-xs text-muted-foreground">{srv.latencyMs}ms</span>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Active Users</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {mockUsers.filter(u => u.status === "active").slice(0, 5).map((user) => (
              <div key={user.id} className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">{user.name}</p>
                  <p className="text-xs text-muted-foreground">{user.team} · {user.role}</p>
                </div>
                <span className="text-xs text-muted-foreground">{user.lastSeen}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
