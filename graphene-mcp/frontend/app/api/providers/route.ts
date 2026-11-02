import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";
import { randomUUID } from "crypto";

const DATA_FILE = path.join(process.cwd(), "data", "providers.json");

async function readProviders() {
  const raw = await fs.readFile(DATA_FILE, "utf-8");
  return JSON.parse(raw);
}

async function writeProviders(providers: unknown[]) {
  await fs.writeFile(DATA_FILE, JSON.stringify(providers, null, 2), "utf-8");
}

export async function GET() {
  try {
    const providers = await readProviders();
    return NextResponse.json(providers);
  } catch {
    return NextResponse.json({ error: "Failed to read providers" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const providers = await readProviders();

    const newProvider = {
      id: `prov-${randomUUID().slice(0, 8)}`,
      name: body.name,
      type: body.type,
      mode: body.type === "slack" ? "Socket Mode" : body.type === "teams" ? "Bot Framework" : "Webhook",
      status: "disconnected",
      channels: 0,
      lastEvent: "never",
      workspaceId: body.workspaceId || "",
      botToken: body.botToken || "",
      appToken: body.appToken || "",
      signingSecret: body.signingSecret || "",
      createdAt: new Date().toISOString().split("T")[0],
    };

    providers.push(newProvider);
    await writeProviders(providers);

    return NextResponse.json(newProvider, { status: 201 });
  } catch {
    return NextResponse.json({ error: "Failed to create provider" }, { status: 500 });
  }
}
