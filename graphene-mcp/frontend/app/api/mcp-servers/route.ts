import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";
import { randomUUID } from "crypto";

const DATA_FILE = path.join(process.cwd(), "data", "mcp-servers.json");

async function readServers() {
  const raw = await fs.readFile(DATA_FILE, "utf-8");
  return JSON.parse(raw);
}

async function writeServers(servers: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(servers, null, 2), "utf-8");
}

export async function GET() {
  try {
    const servers = await readServers();
    return NextResponse.json(servers);
  } catch {
    return NextResponse.json({ error: "Failed to read servers" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const servers = await readServers();

    const now = new Date().toISOString().split("T")[0];
    const newServer = {
      id: `mcp-${randomUUID().slice(0, 8)}`,
      name: body.name,
      endpoint: body.endpoint,
      description: body.description || "",
      authToken: body.authToken || "",
      status: "unknown",
      tools: [],
      latencyMs: 0,
      callsToday: 0,
      agentsUsing: [],
      createdAt: now,
      updatedAt: now,
    };

    servers.push(newServer);
    await writeServers(servers);

    return NextResponse.json(newServer, { status: 201 });
  } catch {
    return NextResponse.json({ error: "Failed to create server" }, { status: 500 });
  }
}
