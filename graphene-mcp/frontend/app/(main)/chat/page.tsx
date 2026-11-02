"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Send, Bot, User, Hash } from "lucide-react";
import { mockChatMessages } from "@/lib/mock-data";

const agentColors: Record<string, string> = {
  "Portfolio Agent": "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  "Advisor Agent": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  "Report Agent": "bg-amber-500/10 text-amber-400 border-amber-500/20",
  "Compliance Agent": "bg-red-500/10 text-red-400 border-red-500/20",
};

const channels = [
  { id: "c1", name: "#portfolio-updates", agent: "Portfolio Agent", provider: "Slack" },
  { id: "c2", name: "#team-alpha-advisory", agent: "Advisor Agent", provider: "Slack" },
  { id: "c3", name: "Alpha Reports Channel", agent: "Report Agent", provider: "Teams" },
  { id: "c4", name: "Investment Bot", agent: "Portfolio Agent", provider: "Telegram" },
];

export default function ChatPage() {
  const [messages, setMessages] = useState(mockChatMessages);
  const [input, setInput] = useState("");
  const [selectedChannel, setSelectedChannel] = useState("c1");

  const channel = channels.find((c) => c.id === selectedChannel)!;

  function sendMessage() {
    if (!input.trim()) return;
    const userMsg = { id: `m-${Date.now()}`, role: "user" as const, content: input, timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) };
    const botMsg = {
      id: `m-${Date.now() + 1}`, role: "assistant" as const,
      agent: channel.agent,
      content: "Processing your request… *(this is a mock response — connect to the backend to get live answers)*",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };
    setMessages((prev) => [...prev, userMsg, botMsg]);
    setInput("");
  }

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-4">
      {/* Channel list */}
      <Card className="w-64 shrink-0 flex flex-col">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Channels</CardTitle>
        </CardHeader>
        <CardContent className="p-2 flex-1 overflow-y-auto space-y-1">
          {channels.map((ch) => (
            <button
              key={ch.id}
              onClick={() => setSelectedChannel(ch.id)}
              className={`w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-left transition-colors ${selectedChannel === ch.id ? "bg-sidebar-accent text-sidebar-accent-foreground" : "hover:bg-muted"}`}
            >
              <Hash className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="truncate font-medium">{ch.name}</p>
                <p className="text-xs text-muted-foreground truncate">{ch.provider}</p>
              </div>
            </button>
          ))}
        </CardContent>
      </Card>

      {/* Chat area */}
      <Card className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <CardHeader className="border-b px-4 py-3 shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Hash className="h-4 w-4 text-muted-foreground" />
              <span className="font-semibold">{channel.name}</span>
              <Badge variant="outline" className="text-xs">{channel.provider}</Badge>
            </div>
            <div className="flex items-center gap-2">
              <Badge className={`text-xs border ${agentColors[channel.agent]}`}>
                <Bot className="h-3 w-3 mr-1" />
                {channel.agent}
              </Badge>
            </div>
          </div>
        </CardHeader>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <div key={msg.id} className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
              <Avatar className="h-8 w-8 shrink-0">
                <AvatarFallback className={`text-xs ${msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
                  {msg.role === "user" ? "SC" : <Bot className="h-4 w-4" />}
                </AvatarFallback>
              </Avatar>
              <div className={`max-w-[70%] ${msg.role === "user" ? "items-end" : "items-start"} flex flex-col gap-1`}>
                {msg.role === "assistant" && "agent" in msg && (
                  <span className="text-xs text-muted-foreground">{(msg as any).agent}</span>
                )}
                <div className={`rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap ${msg.role === "user" ? "bg-primary text-primary-foreground rounded-tr-sm" : "bg-muted rounded-tl-sm"}`}>
                  {msg.content}
                </div>
                <span className="text-xs text-muted-foreground">{msg.timestamp}</span>
              </div>
            </div>
          ))}
        </div>

        {/* Input */}
        <div className="border-t p-4 shrink-0">
          <div className="flex gap-2">
            <Input
              placeholder={`Message ${channel.name}…`}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
              className="flex-1"
            />
            <Button onClick={sendMessage} size="icon">
              <Send className="h-4 w-4" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            Messages are routed to <span className="font-medium">{channel.agent}</span> via {channel.provider}. All invocations are logged.
          </p>
        </div>
      </Card>
    </div>
  );
}
