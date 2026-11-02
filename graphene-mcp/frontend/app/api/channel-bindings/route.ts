import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";
import { randomUUID } from "crypto";

const DATA_FILE = path.join(process.cwd(), "data", "channel-bindings.json");

async function read() {
  return JSON.parse(await fs.readFile(DATA_FILE, "utf-8"));
}

async function write(data: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(data, null, 2), "utf-8");
}

export async function GET() {
  try {
    return NextResponse.json(await read());
  } catch {
    return NextResponse.json({ error: "Failed to read bindings" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const bindings = await read();

    const newBinding = {
      id: `cb-${randomUUID().slice(0, 8)}`,
      channelId: body.channelId,
      channelName: body.channelName,
      provider: body.provider,
      providerId: body.providerId,
      agent: body.agent,
      scope: body.scope,
      createdBy: "admin",
      updatedAt: new Date().toISOString().split("T")[0],
    };

    bindings.push(newBinding);
    await write(bindings);
    return NextResponse.json(newBinding, { status: 201 });
  } catch {
    return NextResponse.json({ error: "Failed to create binding" }, { status: 500 });
  }
}
