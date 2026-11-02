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

// Set one provider as active, deactivate all others
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const providers = await read();
    const target = providers.find((p: { id: string }) => p.id === id);
    if (!target) return NextResponse.json({ error: "Not found" }, { status: 404 });
    if (!target.apiKey) return NextResponse.json({ error: "Configure an API key before activating" }, { status: 400 });

    const updated = providers.map((p: { id: string; active: boolean }) => ({
      ...p,
      active: p.id === id,
    }));

    await write(updated);
    return NextResponse.json({ success: true });
  } catch {
    return NextResponse.json({ error: "Failed to activate provider" }, { status: 500 });
  }
}
