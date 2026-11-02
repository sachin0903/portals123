import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "mcp-servers.json");

async function readServers() {
  const raw = await fs.readFile(DATA_FILE, "utf-8");
  return JSON.parse(raw);
}

async function writeServers(servers: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(servers, null, 2), "utf-8");
}

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const servers = await readServers();
  const server = servers.find((s: { id: string }) => s.id === id);
  if (!server) return NextResponse.json({ error: "Not found" }, { status: 404 });
  return NextResponse.json(server);
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await req.json();
    const servers = await readServers();
    const idx = servers.findIndex((s: { id: string }) => s.id === id);
    if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });

    servers[idx] = {
      ...servers[idx],
      ...body,
      id, // never overwrite id
      updatedAt: new Date().toISOString().split("T")[0],
    };

    await writeServers(servers);
    return NextResponse.json(servers[idx]);
  } catch {
    return NextResponse.json({ error: "Failed to update server" }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const servers = await readServers();
    const filtered = servers.filter((s: { id: string }) => s.id !== id);
    if (filtered.length === servers.length)
      return NextResponse.json({ error: "Not found" }, { status: 404 });

    await writeServers(filtered);
    return NextResponse.json({ success: true });
  } catch {
    return NextResponse.json({ error: "Failed to delete server" }, { status: 500 });
  }
}
