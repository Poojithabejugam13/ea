import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Body, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from .dependencies import get_repo, get_session_mgr, get_ai_agent
from .routes import router as graph_router
from .mcp_server import mcp
from .mcp_server import (
    search_users as mcp_search_users,
    get_users_by_team as mcp_get_users_by_team,
    get_user_schedule as mcp_get_user_schedule,
    get_mutual_free_slots as mcp_get_mutual_free_slots,
    check_conflict_detail as mcp_check_conflict_detail,
)
import time

# Keep logs plain/readable in terminal and suppress noisy ADK internals.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
for noisy_logger in [
    "google.adk.models.google_llm",
    "google.adk.models",
    "google.genai",
]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

app = FastAPI(title="Graph API Mock & Scheduling Agent")

# Simulated Firewall Middleware
class FirewallMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/v1.0"):
            token = request.headers.get("X-Mock-Entra-Token")
            if token != "secret-token":
                return JSONResponse(status_code=403, content={"detail": "Firewall Blocked: Missing or Invalid Entra ID Token"})
        response = await call_next(request)
        return response

app.add_middleware(FirewallMiddleware)

# Add CORS Middleware for Angular Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/agent/process")
async def process_agent_request(payload: dict = Body(...)):
    """
    Endpoint for the Streamlit UI to talk to the AI Agent.
    """
    prompt = payload.get("prompt", "")
    session_id = payload.get("session_id", "default")
    t0 = time.perf_counter()
    agent = get_ai_agent()
    result = agent.process_prompt(prompt, session_id=session_id)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    logging.getLogger("agent.latency").info(
        "agent/process session=%s elapsed_ms=%s prompt_len=%s intent=%s",
        session_id,
        elapsed_ms,
        len(prompt or ""),
        result.get("intent", "unknown"),
    )
    result["latency_ms"] = elapsed_ms
    get_session_mgr().clear_status(session_id)
    return result


@app.get("/agent/status")
async def get_agent_status(session_id: str):
    return {"message": get_session_mgr().get_status(session_id)}

@app.post("/agent/check_conflicts")
async def check_conflicts(payload: dict = Body(...)):
    """
    Endpoint for the UI Wizard to check specific user conflicts.
    """
    user_id = payload.get("user_id")
    start = payload.get("start")
    end = payload.get("end")
    agent = get_ai_agent()
    conflicts = agent.scheduler.check_conflicts(user_id, start, end)
    return {"conflicts": conflicts}


@app.get("/mcp/health")
async def mcp_health():
    """Quick health/timing checks for key MCP tools using mock data."""
    checks = {}

    def run_check(name: str, fn):
        t0 = time.perf_counter()
        try:
            data = fn()
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            checks[name] = {
                "ok": True,
                "elapsed_ms": elapsed_ms,
                "sample_size": len(data) if isinstance(data, list) else 1,
            }
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            checks[name] = {"ok": False, "elapsed_ms": elapsed_ms, "error": str(e)}

    run_check("search_users", lambda: mcp_search_users("rithwika"))
    run_check("get_users_by_team", lambda: mcp_get_users_by_team("Sales"))
    run_check("get_user_schedule", lambda: mcp_get_user_schedule("101", "2026-04-18"))
    run_check("get_mutual_free_slots", lambda: mcp_get_mutual_free_slots(["101", "105"], "2026-04-18", 60))
    run_check("check_conflict_detail", lambda: mcp_check_conflict_detail("101", "2026-04-18T08:30:00Z", "2026-04-18T09:30:00Z"))

    total_ms = round(sum(v["elapsed_ms"] for v in checks.values()), 2)
    return {"overall_ok": all(v.get("ok") for v in checks.values()), "total_elapsed_ms": total_ms, "checks": checks}

@app.get("/teams")
async def get_teams():
    """Return all unique departments for the form dropdown."""
    users = get_repo().get_all_users()
    depts = sorted(list(set(u.department for u in users if u.department)))
    return ["General"] + depts

@app.post("/search/users")
async def search_users_api(payload: dict = Body(...)):
    """Real-time user search for the frontend form."""
    query = payload.get("query", "")
    teams = payload.get("teams", [])
    
    results = get_repo().search_users(query)
    
    # Filter by teams if specified (ignore if "General" in list or list empty)
    if teams:
        if isinstance(teams, str): teams = [teams]
        if "General" not in teams:
            teams_norm = [t.strip().lower() for t in teams]
            results = [u for u in results if u.department and u.department.strip().lower() in teams_norm]
        
    formatted = [
        {
            "id": u.id,
            "name": u.displayName,
            "email": u.mail,
            "jobTitle": u.jobTitle,
            "department": u.department
        }
        for u in results
        if u.id != get_repo().get_organiser().id
    ]
    return formatted

@app.get("/suggestions/subjects")
async def get_subject_suggestions(query: str = ""):
    return get_repo().get_subject_suggestions(query)

@app.get("/suggestions/rooms")
async def get_room_suggestions(query: str = "", start: str = None, end: str = None):
    return get_repo().get_room_suggestions(query, start, end)

@app.get("/suggestions/locations")
async def get_location_suggestions(query: str = ""):
    return get_repo().get_location_suggestions(query)

@app.get("/v1.0/places/microsoft.graph.room")
async def get_graph_rooms():
    """Mock Graph API endpoint for places (rooms)."""
    return get_repo().get_graph_rooms()

app.include_router(graph_router)
app.mount("/mcp", mcp.sse_app())

print("[DEBUG] Main setup complete (lazy).", flush=True)


@app.get("/")
def read_root():
    return {"message": "Mock API is running"}

if __name__ == "__main__":
    import uvicorn
    # Important: use the module path "src.main" if running from the root
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=False)
