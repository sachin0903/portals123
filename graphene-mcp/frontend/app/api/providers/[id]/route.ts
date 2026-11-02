import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "providers.json");

async function readProviders() {
  const raw = await fs.readFile(DATA_FILE, "utf-8");
  return JSON.parse(raw);
}

async function writeProviders(providers: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(providers, null, 2), "utf-8");
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await req.json();
    const providers = await readProviders();
    const idx = providers.findIndex((p: { id: string }) => p.id === id);
    if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });

    providers[idx] = { ...providers[idx], ...body, id };
    await writeProviders(providers);
    return NextResponse.json(providers[idx]);
  } catch {
    return NextResponse.json({ error: "Failed to update provider" }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const providers = await readProviders();
    const filtered = providers.filter((p: { id: string }) => p.id !== id);
    if (filtered.length === providers.length)
      return NextResponse.json({ error: "Not found" }, { status: 404 });

    await writeProviders(filtered);
    return NextResponse.json({ success: true });
  } catch {
    return NextResponse.json({ error: "Failed to delete provider" }, { status: 500 });
  }
}
