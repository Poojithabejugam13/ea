"""
Main FastAPI application entry point for the AI Executive Assistant backend.
Orchestrates:
  - Microsoft Graph API mocked endpoints (users, events, calendar)
  - AI agent endpoint for processing scheduling requests
  - MCP (Model Context Protocol) server for structured tool execution
  - Redis-backed session management
  - CORS support for Angular frontend
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Body, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from .dependencies import get_repo, get_session_mgr
from .routes import router as graph_router
from .mcp_server import mcp

app = FastAPI(title="Graph API Mock & Scheduling Agent")

# Simulated Firewall Middleware — validates Entra ID tokens for Graph API routes
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
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval' 'unsafe-inline' https://*.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' http://localhost:* ws://localhost:* https://*.googleapis.com;"
    )
    return response

@app.post("/agent/process")
async def process_agent_request(payload: dict = Body(...)):
    """
    Endpoint for the Streamlit UI to talk to the AI Agent.
    """
    prompt = payload.get("prompt", "")
    # Lazy init AIAgent to avoid hang
    from .services import AIAgent
    agent = AIAgent(get_repo(), get_session_mgr())
    result = agent.process_prompt(prompt)
    return result

@app.post("/agent/check_conflicts")
async def check_conflicts(payload: dict = Body(...)):
    """
    Endpoint for the UI Wizard to check specific user conflicts.
    """
    user_id = payload.get("user_id")
    start = payload.get("start")
    end = payload.get("end")
    # Lazy init AIAgent for conflict check
    from .services import AIAgent
    agent = AIAgent(get_repo(), get_session_mgr())
    conflicts = agent.scheduler.check_conflicts(user_id, start, end)
    return {"conflicts": conflicts}

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
