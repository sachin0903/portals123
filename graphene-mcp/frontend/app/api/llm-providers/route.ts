import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "llm-providers.json");

async function read() {
  return JSON.parse(await fs.readFile(DATA_FILE, "utf-8"));
}

export async function GET() {
  try {
    const providers = await read();
    // Never expose the raw API key to the frontend — only whether it's configured
    const safe = providers.map((p: { apiKey: string; [key: string]: unknown }) => ({
      ...p,
      apiKey: undefined,
      keyConfigured: !!p.apiKey,
    }));
    return NextResponse.json(safe);
  } catch {
    return NextResponse.json({ error: "Failed to read LLM providers" }, { status: 500 });
  }
}
