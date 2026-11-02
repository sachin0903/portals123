"""
Portfolio MCP Server
---
Run locally:  python server.py
MCP endpoint: http://localhost:8001/mcp
Tools REST:   http://localhost:8001/tools   (used by Graphene admin for discovery)
Health:       http://localhost:8001/health
"""

from mcp.server.fastmcp import FastMCP
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

mcp = FastMCP("portfolio-mcp")

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

POSITIONS = [
    {"symbol": "AAPL",  "shares": 1500, "avg_cost": 145.20, "current_price": 189.50, "value": 284_250.00, "pnl":  66_450.00, "pnl_pct":  30.5},
    {"symbol": "MSFT",  "shares":  800, "avg_cost": 280.00, "current_price": 415.30, "value": 332_240.00, "pnl": 108_240.00, "pnl_pct":  48.3},
    {"symbol": "NVDA",  "shares":  500, "avg_cost": 220.00, "current_price": 875.40, "value": 437_700.00, "pnl": 327_700.00, "pnl_pct": 297.9},
    {"symbol": "GOOGL", "shares":  300, "avg_cost": 125.00, "current_price": 172.80, "value":  51_840.00, "pnl":  14_340.00, "pnl_pct":  38.2},
    {"symbol": "TSLA",  "shares":  400, "avg_cost": 250.00, "current_price": 175.30, "value":  70_120.00, "pnl": -29_880.00, "pnl_pct": -29.9},
]

PNL = {
    "total_aum":        284_750_000,
    "daily_pnl":          1_234_567,
    "daily_pnl_pct":           0.43,
    "weekly_pnl":         3_891_200,
    "weekly_pnl_pct":          1.38,
    "monthly_pnl":       12_450_000,
    "monthly_pnl_pct":         4.57,
    "active_positions":          147,
}

HISTORY = [
    {"date": "2026-01-01", "aum": 260_000_000, "daily_return":  0.12},
    {"date": "2026-02-01", "aum": 265_500_000, "daily_return":  0.21},
    {"date": "2026-03-01", "aum": 258_000_000, "daily_return": -0.28},
    {"date": "2026-04-01", "aum": 271_000_000, "daily_return":  0.34},
    {"date": "2026-05-01", "aum": 275_300_000, "daily_return":  0.15},
    {"date": "2026-06-01", "aum": 284_750_000, "daily_return":  0.18},
]

TRANSACTIONS = [
    {"id": "txn-001", "date": "2026-06-08", "symbol": "AAPL",  "type": "BUY",  "shares": 100, "price": 189.50, "total":  18_950.00},
    {"id": "txn-002", "date": "2026-06-07", "symbol": "NVDA",  "type": "SELL", "shares":  50, "price": 882.10, "total":  44_105.00},
    {"id": "txn-003", "date": "2026-06-06", "symbol": "MSFT",  "type": "BUY",  "shares": 200, "price": 413.20, "total":  82_640.00},
    {"id": "txn-004", "date": "2026-06-05", "symbol": "TSLA",  "type": "SELL", "shares":  75, "price": 178.90, "total":  13_417.50},
    {"id": "txn-005", "date": "2026-06-04", "symbol": "GOOGL", "type": "BUY",  "shares":  50, "price": 170.20, "total":   8_510.00},
]

# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_positions() -> list:
    """Get all current portfolio positions with symbol, shares, cost basis, current value, and P&L"""
    return POSITIONS


@mcp.tool()
def get_pnl() -> dict:
    """Get portfolio profit and loss summary including daily, weekly, and monthly performance and total AUM"""
    return PNL


@mcp.tool()
def get_history() -> list:
    """Get historical portfolio AUM and daily returns over the past 6 months"""
    return HISTORY


@mcp.tool()
def get_transactions() -> list:
    """Get the 5 most recent portfolio transactions including buys and sells"""
    return TRANSACTIONS


# ---------------------------------------------------------------------------
# Tools metadata — served at GET /tools for Graphene admin discovery
# Kept in sync with the @mcp.tool() registrations above
# ---------------------------------------------------------------------------

TOOLS_META = [
    {"name": "get_positions",    "description": "Get all current portfolio positions with symbol, shares, cost basis, current value, and P&L"},
    {"name": "get_pnl",          "description": "Get portfolio profit and loss summary including daily, weekly, and monthly performance and total AUM"},
    {"name": "get_history",      "description": "Get historical portfolio AUM and daily returns over the past 6 months"},
    {"name": "get_transactions", "description": "Get the 5 most recent portfolio transactions including buys and sells"},
]

# ---------------------------------------------------------------------------
# REST endpoints (not MCP protocol — used only by Graphene admin UI)
# ---------------------------------------------------------------------------

async def health_endpoint(request):
    return JSONResponse({"status": "healthy", "server": "portfolio-mcp", "tool_count": len(TOOLS_META)})


async def tools_endpoint(request):
    return JSONResponse({"tools": TOOLS_META})


TOOL_FUNCS = {
    "get_positions":    get_positions,
    "get_pnl":          get_pnl,
    "get_history":      get_history,
    "get_transactions": get_transactions,
}

async def call_tool_endpoint(request):
    """REST shim so the Graphene bot can call tools without the MCP SDK."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    name = body.get("name")
    arguments = body.get("arguments", {})

    if name not in TOOL_FUNCS:
        return JSONResponse({"error": f"Unknown tool: {name!r}"}, status_code=404)

    try:
        result = TOOL_FUNCS[name](**arguments)
        return JSONResponse({"result": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Build combined app: FastMCP at /mcp  +  REST at /health, /tools, /call
# FastMCP.streamable_http_app() returns the Starlette ASGI app.
# ---------------------------------------------------------------------------

mcp_asgi = mcp.streamable_http_app()

app = Starlette(
    routes=[
        Route("/health", health_endpoint),
        Route("/tools",  tools_endpoint),
        Route("/call",   call_tool_endpoint, methods=["POST"]),
        Mount("/mcp",    app=mcp_asgi),
    ],
    middleware=[
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    ],
)

if __name__ == "__main__":
    print("Portfolio MCP server starting on http://0.0.0.0:8001")
    print("  MCP endpoint : http://localhost:8001/mcp")
    print("  Tools REST   : http://localhost:8001/tools")
    print("  Health       : http://localhost:8001/health")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
