import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "providers.json");

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const raw = await fs.readFile(DATA_FILE, "utf-8");
  const providers: Array<{
    id: string;
    type: string;
    botToken: string;
    workspaceId: string;
    status: string;
  }> = JSON.parse(raw);

  const provider = providers.find((p) => p.id === id);
  if (!provider) return NextResponse.json({ error: "Provider not found" }, { status: 404 });

  if (provider.type === "slack") {
    if (!provider.botToken) {
      return NextResponse.json({ ok: false, error: "Bot token not configured" });
    }

    try {
      const res = await fetch("https://slack.com/api/auth.test", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${provider.botToken}`,
          "Content-Type": "application/json",
        },
      });

      const data = await res.json();

      if (data.ok) {
        // Auto-update workspaceId and status if auth succeeds
        const idx = providers.findIndex((p) => p.id === id);
        providers[idx] = {
          ...providers[idx],
          status: "connected",
          workspaceId: providers[idx].workspaceId || data.team_id,
        };
        await fs.writeFile(DATA_FILE, JSON.stringify(providers, null, 2), "utf-8");

        return NextResponse.json({
          ok: true,
          workspace: data.team,
          workspaceId: data.team_id,
          botUser: data.user,
          botUserId: data.user_id,
          botId: data.bot_id,
        });
      } else {
        return NextResponse.json({ ok: false, error: data.error });
      }
    } catch (err) {
      return NextResponse.json({ ok: false, error: `Network error: ${(err as Error).message}` });
    }
  }

  return NextResponse.json({ ok: false, error: "Test not supported for this provider type" });
}
