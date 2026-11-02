import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";
import { randomUUID } from "crypto";

const DATA_FILE = path.join(process.cwd(), "data", "users.json");

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
    return NextResponse.json({ error: "Failed to read users" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    if (!body.name || !body.slackUserId) {
      return NextResponse.json({ error: "name and slackUserId are required" }, { status: 400 });
    }
    const users = await read();
    const duplicate = users.find((u: { slackUserId: string }) => u.slackUserId === body.slackUserId);
    if (duplicate) {
      return NextResponse.json({ error: "A user with this Slack User ID already exists" }, { status: 409 });
    }
    const user = {
      id: `user-${randomUUID().slice(0, 8)}`,
      name: body.name,
      slackUserId: body.slackUserId,
      platform: body.platform ?? "slack",
      agents: body.agents ?? [],
      role: body.role ?? "member",
      status: "active",
      createdAt: new Date().toISOString().slice(0, 10),
    };
    users.push(user);
    await write(users);
    return NextResponse.json(user, { status: 201 });
  } catch {
    return NextResponse.json({ error: "Failed to create user" }, { status: 500 });
  }
}
