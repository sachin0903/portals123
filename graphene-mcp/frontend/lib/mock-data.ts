// Mock data for the Graphene ChatOps AI Platform

export const mockPortfolioStats = {
  totalAUM: 284_750_000,
  dailyPnL: 1_234_567,
  dailyPnLPct: 0.43,
  weeklyPnL: 3_891_200,
  weeklyPnLPct: 1.38,
  activePositions: 147,
  alerts: 3,
};

export const mockPnLHistory = [
  { date: "Jan", value: 260_000_000 },
  { date: "Feb", value: 265_500_000 },
  { date: "Mar", value: 258_000_000 },
  { date: "Apr", value: 271_000_000 },
  { date: "May", value: 275_300_000 },
  { date: "Jun", value: 284_750_000 },
];

export const mockAgentActivity = [
  { name: "Portfolio", calls: 1240, tokens: 890_000 },
  { name: "Advisor", calls: 830, tokens: 1_200_000 },
  { name: "Report", calls: 145, tokens: 450_000 },
  { name: "Compliance", calls: 32, tokens: 120_000 },
];

// Chat providers
export const mockProviders = [
  {
    id: "prov-1",
    name: "Slack — Graphene Workspace",
    type: "slack",
    status: "connected",
    mode: "Socket Mode",
    channels: 12,
    lastEvent: "2 min ago",
    tenantId: "T04ABCDEF12",
  },
  {
    id: "prov-2",
    name: "Microsoft Teams — Corp",
    type: "teams",
    status: "connected",
    mode: "Bot Framework",
    channels: 8,
    lastEvent: "5 min ago",
    tenantId: "72f988bf-86f1-41af-91ab-2d7cd011db47",
  },
  {
    id: "prov-3",
    name: "Telegram — Investment Bot",
    type: "telegram",
    status: "connected",
    mode: "Webhook",
    channels: 4,
    lastEvent: "12 min ago",
    tenantId: "@graphene_invest_bot",
  },
  {
    id: "prov-4",
    name: "Web UI",
    type: "web",
    status: "connected",
    mode: "WebSocket",
    channels: 0,
    lastEvent: "just now",
    tenantId: "wss://app.graphene.io",
  },
];

// Channel bindings
export const mockChannelBindings = [
  { id: "cb-1", channelId: "C04PORTFOLIO", channelName: "#portfolio-updates", provider: "Slack", agent: "Portfolio Agent", scope: "org", createdBy: "sarah.admin", updatedAt: "2026-05-10" },
  { id: "cb-2", channelId: "C04ADVISORY", channelName: "#team-alpha-advisory", provider: "Slack", agent: "Advisor Agent", scope: "team", createdBy: "sarah.admin", updatedAt: "2026-05-12" },
  { id: "cb-3", channelId: "19:abc123@thread.v2", channelName: "Alpha Reports Channel", provider: "Teams", agent: "Report Agent", scope: "team", createdBy: "john.admin", updatedAt: "2026-05-15" },
  { id: "cb-4", channelId: "-1001234567890", channelName: "Compliance Group", provider: "Telegram", agent: "Compliance Agent", scope: "admin", createdBy: "sarah.admin", updatedAt: "2026-06-01" },
  { id: "cb-5", channelId: "C04GENERAL", channelName: "#general", provider: "Slack", agent: "Portfolio Agent", scope: "org", createdBy: "john.admin", updatedAt: "2026-04-20" },
];

// Agents
export const mockAgents = [
  {
    id: "agent-portfolio",
    name: "Portfolio Agent",
    scope: "org",
    status: "active",
    model: "claude-sonnet-4-6",
    tokenBudget: 50_000,
    mcpServers: ["Portfolio MCP"],
    invocationsToday: 412,
    avgLatencyMs: 1240,
    systemPromptPreview: "You are a portfolio analyst. Provide insights on positions, P&L, and risk metrics for the organisation...",
  },
  {
    id: "agent-advisor",
    name: "Advisor Agent",
    scope: "team",
    status: "active",
    model: "claude-sonnet-4-6",
    tokenBudget: 80_000,
    mcpServers: ["Portfolio MCP", "Market Data MCP"],
    invocationsToday: 287,
    avgLatencyMs: 2100,
    systemPromptPreview: "You are an investment advisor for a specific team. Provide tailored recommendations based on the team mandate...",
  },
  {
    id: "agent-report",
    name: "Report Agent",
    scope: "team",
    status: "active",
    model: "claude-sonnet-4-6",
    tokenBudget: 100_000,
    mcpServers: ["Portfolio MCP", "Market Data MCP", "Report Generator MCP"],
    invocationsToday: 54,
    avgLatencyMs: 5800,
    systemPromptPreview: "You are a report generation assistant. Orchestrate PDF and Excel report creation based on portfolio and market data...",
  },
  {
    id: "agent-compliance",
    name: "Compliance Agent",
    scope: "admin",
    status: "active",
    model: "claude-opus-4-8",
    tokenBudget: 200_000,
    mcpServers: ["Audit Logger MCP"],
    invocationsToday: 8,
    avgLatencyMs: 3400,
    systemPromptPreview: "You are a compliance officer assistant. Surface audit events, flag anomalies, and generate compliance reports...",
  },
];

// MCP Servers
export const mockMcpServers = [
  { id: "mcp-portfolio", name: "Portfolio MCP", endpoint: "http://portfolio-mcp:8001/mcp", status: "healthy", tools: ["get_positions", "get_pnl", "get_history", "get_transactions"], latencyMs: 45, callsToday: 8_240, agentsUsing: ["Portfolio Agent", "Advisor Agent", "Report Agent"] },
  { id: "mcp-market", name: "Market Data MCP", endpoint: "http://market-mcp:8002/mcp", status: "healthy", tools: ["get_price", "get_news", "get_fundamentals", "get_historical"], latencyMs: 128, callsToday: 5_670, agentsUsing: ["Advisor Agent", "Report Agent"] },
  { id: "mcp-report", name: "Report Generator MCP", endpoint: "http://report-mcp:8003/mcp", status: "healthy", tools: ["generate_pdf", "generate_excel", "list_reports", "get_download_url"], latencyMs: 2200, callsToday: 215, agentsUsing: ["Report Agent"] },
  { id: "mcp-audit", name: "Audit Logger MCP", endpoint: "http://audit-mcp:8004/mcp", status: "healthy", tools: ["log_event", "get_events", "get_event_by_id"], latencyMs: 22, callsToday: 12_890, agentsUsing: ["Compliance Agent", "all agents"] },
];

// Users
export const mockUsers = [
  { id: "u-1", name: "Sarah Chen", email: "sarah.chen@graphene.io", role: "admin", team: "Platform", provider: "Slack", status: "active", lastSeen: "just now" },
  { id: "u-2", name: "James Harrington", email: "james.h@graphene.io", role: "editor", team: "Alpha", provider: "Teams", status: "active", lastSeen: "3 min ago" },
  { id: "u-3", name: "Priya Mehta", email: "priya.m@graphene.io", role: "member", team: "Alpha", provider: "Slack", status: "active", lastSeen: "18 min ago" },
  { id: "u-4", name: "David Okonkwo", email: "david.o@graphene.io", role: "member", team: "Beta", provider: "Telegram", status: "active", lastSeen: "1 hr ago" },
  { id: "u-5", name: "Emma Walsh", email: "emma.w@graphene.io", role: "viewer", team: "Beta", provider: "Teams", status: "active", lastSeen: "2 hr ago" },
  { id: "u-6", name: "Luca Bianchi", email: "luca.b@graphene.io", role: "member", team: "Gamma", provider: "Slack", status: "inactive", lastSeen: "5 days ago" },
];

export const mockTeams = [
  { id: "t-1", name: "Alpha", members: 12, lead: "James Harrington", agents: ["Advisor Agent", "Report Agent"], createdAt: "2026-01-10" },
  { id: "t-2", name: "Beta", members: 8, lead: "David Okonkwo", agents: ["Advisor Agent", "Report Agent"], createdAt: "2026-02-14" },
  { id: "t-3", name: "Gamma", members: 5, lead: "Luca Bianchi", agents: ["Advisor Agent"], createdAt: "2026-03-22" },
  { id: "t-4", name: "Platform", members: 3, lead: "Sarah Chen", agents: ["Compliance Agent"], createdAt: "2026-01-01" },
];

// Audit logs
export const mockAuditLogs = [
  { id: "evt-001", timestamp: "2026-06-08 14:32:11", user: "james.h@graphene.io", agent: "Advisor Agent", channel: "#team-alpha-advisory", provider: "Slack", action: "invoke", outcome: "success", tokens: 3840, latencyMs: 2100 },
  { id: "evt-002", timestamp: "2026-06-08 14:28:03", user: "priya.m@graphene.io", agent: "Portfolio Agent", channel: "#general", provider: "Slack", action: "invoke", outcome: "success", tokens: 1240, latencyMs: 980 },
  { id: "evt-003", timestamp: "2026-06-08 14:15:47", user: "david.o@graphene.io", agent: "Compliance Agent", channel: "Compliance Group", provider: "Telegram", action: "invoke", outcome: "denied", tokens: 0, latencyMs: 12 },
  { id: "evt-004", timestamp: "2026-06-08 13:58:22", user: "emma.w@graphene.io", agent: "Report Agent", channel: "Alpha Reports Channel", provider: "Teams", action: "invoke", outcome: "success", tokens: 8200, latencyMs: 5800 },
  { id: "evt-005", timestamp: "2026-06-08 13:45:01", user: "sarah.chen@graphene.io", agent: "Compliance Agent", channel: "#compliance-internal", provider: "Slack", action: "invoke", outcome: "success", tokens: 12400, latencyMs: 4200 },
  { id: "evt-006", timestamp: "2026-06-08 13:30:15", user: "luca.b@graphene.io", agent: "Advisor Agent", channel: "#team-gamma-advisory", provider: "Slack", action: "invoke", outcome: "success", tokens: 4100, latencyMs: 2300 },
  { id: "evt-007", timestamp: "2026-06-08 13:12:44", user: "james.h@graphene.io", agent: "Report Agent", channel: "Alpha Reports Channel", provider: "Teams", action: "invoke", outcome: "success", tokens: 6700, latencyMs: 4800 },
  { id: "evt-008", timestamp: "2026-06-08 12:55:30", user: "unknown@vendor.com", agent: "Portfolio Agent", channel: "#shared-external", provider: "Slack", action: "invoke", outcome: "denied", tokens: 0, latencyMs: 8 },
];

// Chat messages
export const mockChatMessages = [
  { id: "m-1", role: "user", content: "What's the current P&L for the Alpha team portfolio today?", timestamp: "14:30" },
  { id: "m-2", role: "assistant", agent: "Portfolio Agent", content: "The Alpha team portfolio is up **+$847,230** (+0.62%) today. Top performers are AAPL (+2.1%), MSFT (+1.8%), and NVDA (+3.4%). Biggest drag is TSLA (-1.9%).", timestamp: "14:30" },
  { id: "m-3", role: "user", content: "Generate a weekly performance report in PDF", timestamp: "14:31" },
  { id: "m-4", role: "assistant", agent: "Report Agent", content: "Generating your weekly performance report... \n\n✅ Report created: **Alpha_Weekly_2026-06-08.pdf**\n\n[Download Report](#) — link expires in 15 minutes.", timestamp: "14:32" },
  { id: "m-5", role: "user", content: "What are the latest news headlines for NVDA?", timestamp: "14:33" },
  { id: "m-6", role: "assistant", agent: "Advisor Agent", content: "Here are the latest NVDA headlines:\n\n1. **NVDA beats earnings** — Q2 revenue up 122% YoY (Reuters, 2h ago)\n2. **AI chip demand surge** — Data center revenue hits $22.6B (Bloomberg, 3h ago)\n3. **Analyst upgrades** — Goldman raises target to $180 (FT, 4h ago)", timestamp: "14:33" },
];

// LLM providers
export const mockLLMProviders = [
  { id: "llm-1", name: "Anthropic Claude", models: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"], active: true, keyConfigured: true, dataResidency: "US/EU (SaaS)", usageToday: 2_840_000, costToday: 42.60 },
  { id: "llm-2", name: "OpenAI", models: ["gpt-4o", "gpt-4-turbo", "gpt-4o-mini"], active: false, keyConfigured: true, dataResidency: "US (SaaS)", usageToday: 0, costToday: 0 },
  { id: "llm-3", name: "AWS Bedrock", models: ["anthropic.claude-3-5-sonnet", "anthropic.claude-3-opus"], active: false, keyConfigured: false, dataResidency: "Customer VPC (recommended for regulated)", usageToday: 0, costToday: 0 },
];
