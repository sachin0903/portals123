import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "llm-providers.json");

async function read() {
  return JSON.parse(await fs.readFile(DATA_FILE, "utf-8"));
}

async function write(data: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(data, null, 2), "utf-8");
}

// Update API key and/or active model
export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await req.json();
    const providers = await read();
    const idx = providers.findIndex((p: { id: string }) => p.id === id);
    if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });

    // Only update apiKey if a non-empty value was provided (blank = keep existing)
    const existing = providers[idx];
    providers[idx] = {
      ...existing,
      activeModel: body.activeModel ?? existing.activeModel,
      apiKey: body.apiKey ? body.apiKey : existing.apiKey,
    };

    await write(providers);
    return NextResponse.json({ ...providers[idx], apiKey: undefined, keyConfigured: !!providers[idx].apiKey });
  } catch {
    return NextResponse.json({ error: "Failed to update provider" }, { status: 500 });
  }
}
