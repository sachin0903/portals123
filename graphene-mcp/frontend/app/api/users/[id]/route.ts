import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "users.json");

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
    const users = await read();
    const idx = users.findIndex((u: { id: string }) => u.id === id);
    if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });
    users[idx] = { ...users[idx], ...body, id };
    await write(users);
    return NextResponse.json(users[idx]);
  } catch {
    return NextResponse.json({ error: "Failed to update user" }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const users = await read();
    const filtered = users.filter((u: { id: string }) => u.id !== id);
    if (filtered.length === users.length) return NextResponse.json({ error: "Not found" }, { status: 404 });
    await write(filtered);
    return NextResponse.json({ success: true });
  } catch {
    return NextResponse.json({ error: "Failed to delete user" }, { status: 500 });
  }
}
