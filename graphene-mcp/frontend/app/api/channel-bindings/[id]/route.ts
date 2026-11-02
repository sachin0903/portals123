import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "channel-bindings.json");

async function read() {
  return JSON.parse(await fs.readFile(DATA_FILE, "utf-8"));
}

async function write(data: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(data, null, 2), "utf-8");
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await req.json();
    const bindings = await read();
    const idx = bindings.findIndex((b: { id: string }) => b.id === id);
    if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });

    bindings[idx] = {
      ...bindings[idx],
      ...body,
      id,
      updatedAt: new Date().toISOString().split("T")[0],
    };
    await write(bindings);
    return NextResponse.json(bindings[idx]);
  } catch {
    return NextResponse.json({ error: "Failed to update binding" }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const bindings = await read();
    const filtered = bindings.filter((b: { id: string }) => b.id !== id);
    if (filtered.length === bindings.length)
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    await write(filtered);
    return NextResponse.json({ success: true });
  } catch {
    return NextResponse.json({ error: "Failed to delete binding" }, { status: 500 });
  }
}
