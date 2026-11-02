import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "mcp-servers.json");

interface McpTool {
  name: string;
  description?: string;
  inputSchema?: unknown;
}

// Parse either plain JSON or an SSE stream (text/event-stream) from an MCP server response.
async function parseMcpResponse(res: Response): Promise<unknown> {
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("text/event-stream")) {
    const text = await res.text();
    const dataLine = text.split("\n").find((l) => l.startsWith("data: "));
    if (!dataLine) throw new Error("Empty SSE response");
    return JSON.parse(dataLine.slice(6));
  }
  return res.json();
}

async function mcpPost(url: string, body: object, sessionId?: string) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;

  const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  const data = await parseMcpResponse(res);
  const sid = res.headers.get("mcp-session-id") || sessionId;
  return { data, sessionId: sid };
}

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  // Load the server record so we know the endpoint
  const raw = await fs.readFile(DATA_FILE, "utf-8");
  const servers: Array<{ id: string; endpoint: string; authToken?: string }> = JSON.parse(raw);
  const server = servers.find((s) => s.id === id);
  if (!server) return NextResponse.json({ error: "Server not found" }, { status: 404 });

  // Try the /tools REST shortcut first (portfolio-mcp exposes this for fast discovery)
  const baseUrl = server.endpoint.replace(/\/mcp$/, "");
  try {
    const toolsRes = await fetch(`${baseUrl}/tools`, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(5000),
    });
    if (toolsRes.ok) {
      const { tools } = await toolsRes.json();
      return NextResponse.json({ tools });
    }
  } catch {
    // fall through to MCP protocol
  }

  // Fall back to full MCP protocol: initialize → tools/list
  try {
    const mcpUrl = server.endpoint;

    const { data: initData, sessionId } = await mcpPost(
      mcpUrl,
      {
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocolVersion: "2024-11-05",
          capabilities: {},
          clientInfo: { name: "graphene-admin", version: "1.0.0" },
        },
      },
      undefined,
    );

    if ((initData as { error?: unknown }).error)
      throw new Error(`Initialize failed: ${JSON.stringify((initData as { error: unknown }).error)}`);

    const { data: toolsData } = await mcpPost(
      mcpUrl,
      { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} },
      sessionId ?? undefined,
    );

    const tools: McpTool[] = (toolsData as { result?: { tools?: McpTool[] } }).result?.tools ?? [];
    return NextResponse.json({
      tools: tools.map((t) => ({ name: t.name, description: t.description ?? "" })),
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Discovery failed: ${(err as Error).message}` },
      { status: 502 },
    );
  }
}
