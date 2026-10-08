import json
import math
import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from contextlib import asynccontextmanager
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field
from starlette.middleware.cors import CORSMiddleware

import ai as ailib
import aptcontext as aptlib
import aptspeed as speedlib
import aptsuggest as suggestlib
import bim as bimlib
import citations as citelib
import auth as authlib
import codesearch as codesearchlib
import datahealth as datahealthlib
import engine
import engineering as englib
import finance as financelib
import gis as gislib
import iscodes as iscodes
import optimise as optlib
import planopt as planoptlib
import layout as layoutlib
import reports as reportlib
import schedule as schedlib
import siteplan as siteplanlib
from defaults import (default_project, default_tower, floor_layout_entry,
                      towers_from_site_layout)
from residential_defaults import (
    new_residential_policy, parking_summary, summarize_units, tower_index,
    update_tower_parking, unit_key,
)
import aifloorplan
import recommend as reclib
import collaboration as collablib
import equipment as equipengine
import utility_network as utilnetengine
import generative_design as gendesign
import autonomous_planning as autoplanning
import smart_city as smartcitylib
import digital_twin as digitaltwinlib
import ai_os as aioslib
import procurement_market as procurementlib
import urban_sustainability as urbansustlib
from input_validation import ProjectInputError, validate_project

client = AsyncIOMotorClient(
    os.environ.get('MONGO_URL', 'mongodb://localhost:27017'),
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=10000,
    maxPoolSize=50,
)
db = client[os.environ.get('DB_NAME', 'aptimizer')]

# The interactive docs publish the whole API surface -- every route, every schema, every
# field name -- to anyone who opens /docs. The endpoints behind them still enforce auth, so
# this is a map rather than a hole, but a public deployment has no reason to hand one out.
# Off unless explicitly enabled, because the failure mode of the other default is silent:
# nobody notices the docs are public until someone reads them.
_API_DOCS = (os.environ.get("ENABLE_API_DOCS") or "").strip().lower() in {"1", "true", "yes", "on"}

logger = logging.getLogger("aptimizer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    authlib.check_secret()
    # Indexes and the admin account are set up independently: a failing index (for example
    # a database user without index rights) must never leave the deployment without an admin.
    try:
        await db.users.create_index("email", unique=True)
        await db.login_attempts.create_index("identifier")
        await db.projects.create_index("owner_id")
        await db.shares.create_index([("project_id", 1), ("user_id", 1)])
        await db.activity.create_index("project_id")
    except Exception as e:
        logger.warning("Startup index creation encountered issue: %s", e)
    try:
        # Hosting dashboards keep whatever was pasted, quotes and stray spaces included; a
        # password stored as "Aptimizer@123 " can never be typed at the sign-in screen.
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@aptimizer.com").strip().strip("\"'").strip().lower()
        admin_password = os.environ.get("ADMIN_PASSWORD", "Aptimizer@123").strip().strip("\"'").strip()
        await db.login_attempts.delete_many({"identifier": {"$regex": f":{re.escape(admin_email)}$"}})
        existing = await db.users.find_one({"email": admin_email})
        if not existing:
            await db.users.insert_one({
                "name": "Aptimizer Admin", "email": admin_email,
                "password_hash": authlib.hash_password(admin_password), "role": "admin",
                "org": "Aptimizer", "contact": "", "created_at": now_iso()})
            logger.info("Seeded admin user %s", admin_email)
        elif (not authlib.verify_password(admin_password, existing.get("password_hash", ""))
              or existing.get("role") != "admin"):
            await db.users.update_one(
                {"email": admin_email},
                {"$set": {"password_hash": authlib.hash_password(admin_password), "role": "admin"}},
            )
            logger.info("Updated admin credentials for %s", admin_email)
        # WARNING level so it shows in the host's default log view (Render shows warnings).
        logger.warning("Admin account ready: %s (password from %s)", admin_email,
                       "ADMIN_PASSWORD" if os.environ.get("ADMIN_PASSWORD") else "built-in default")
    except Exception as e:
        logger.warning("Startup admin setup FAILED: %s", e)
    yield
    client.close()


app = FastAPI(title="Aptimizer API",
              docs_url="/docs" if _API_DOCS else None,
              redoc_url="/redoc" if _API_DOCS else None,
              openapi_url="/openapi.json" if _API_DOCS else None,
              lifespan=lifespan)
api = APIRouter(prefix="/api")


@app.exception_handler(ProjectInputError)
async def project_input_error(request: Request, exc: ProjectInputError):
    return JSONResponse(status_code=422, content={"detail": str(exc), "issues": exc.issues})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc) if _API_DOCS or os.environ.get("ENV") == "development" else "An internal server error occurred.",
            "error_type": type(exc).__name__,
            "path": request.url.path,
        },
    )


@api.get("/health")
@app.get("/health")
async def health_check():
    """Health check verifying database connection and service responsiveness."""
    try:
        await client.admin.command("ping")
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "database": db_status,
        "timestamp": now_iso(),
    }


WRITE_ROLES = ("admin", "engineer")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Resource not found")


async def get_current_user(request: Request) -> dict:
    return await authlib.current_user_from_request(request, db)


# ---------------------------------------------------------------- auth models
class RegisterIn(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=6)
    role: str = "engineer"
    org: str = ""
    contact: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ProfileIn(BaseModel):
    name: Optional[str] = None
    org: Optional[str] = None
    contact: Optional[str] = None


class ProjectIn(BaseModel):
    name: str
    client: str = ""
    location: str = ""
    plot_reference: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class ProjectPatch(BaseModel):
    updates: Dict[str, Any]
    note: Optional[str] = None
    # The revision the client believes it is editing. Omitted by callers that predate the
    # check, which then keep the old last-writer-wins behaviour rather than being locked
    # out; a client that sends one gets a 409 instead of silently losing the other edit.
    rev: Optional[int] = None


class VersionIn(BaseModel):
    label: str


class ShareIn(BaseModel):
    email: EmailStr
    role: str = "viewer"


# ---------------------------------------------------------------- auth routes
@api.post("/auth/register")
async def register(body: RegisterIn, response: Response):
    email = body.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    role = body.role if body.role in authlib.ROLES else "engineer"
    doc = {"name": body.name, "email": email, "password_hash": authlib.hash_password(body.password),
           "role": role, "org": body.org, "contact": body.contact, "created_at": now_iso()}
    res = await db.users.insert_one(doc)
    doc["_id"] = res.inserted_id
    access = authlib.create_access_token(str(res.inserted_id), email)
    authlib.set_auth_cookies(response, access, authlib.create_refresh_token(str(res.inserted_id)))
    return {"user": authlib.public_user(doc), "access_token": access}


@api.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    email = body.email.lower()
    ip = request.client.host if request.client else "unknown"
    ident = f"{ip}:{email}"
    attempt = await db.login_attempts.find_one({"identifier": ident})
    if attempt and attempt.get("count", 0) >= 5:
        locked_at = datetime.fromisoformat(attempt["last_at"])
        if (datetime.now(timezone.utc) - locked_at).total_seconds() < 900:
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    user = await db.users.find_one({"email": email})
    if not user or not authlib.verify_password(body.password, user.get("password_hash", "")):
        await db.login_attempts.update_one(
            {"identifier": ident},
            {"$inc": {"count": 1}, "$set": {"last_at": now_iso()}}, upsert=True)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await db.login_attempts.delete_one({"identifier": ident})
    access = authlib.create_access_token(str(user["_id"]), email)
    authlib.set_auth_cookies(response, access, authlib.create_refresh_token(str(user["_id"])))
    return {"user": authlib.public_user(user), "access_token": access}


@api.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = authlib.decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = await db.users.find_one({"_id": oid(payload["sub"])})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    access = authlib.create_access_token(str(user["_id"]), user["email"])
    authlib.set_auth_cookies(response, access, authlib.create_refresh_token(str(user["_id"])))
    return {"user": authlib.public_user(user), "access_token": access}


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return authlib.public_user(user)


@api.put("/users/me")
async def update_profile(body: ProfileIn, user: dict = Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        await db.users.update_one({"_id": user["_id"]}, {"$set": updates})
    fresh = await db.users.find_one({"_id": user["_id"]})
    return authlib.public_user(fresh)


@api.get("/users")
async def list_users(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = await db.users.find().sort("created_at", -1).to_list(500)
    return [authlib.public_user(u) for u in users]


# ---------------------------------------------------------------- helpers
def serialize_project(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    return d


async def load_project(project_id: str, user: dict, write: bool = False) -> dict:
    proj = await db.projects.find_one({"_id": oid(project_id)})
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    role = None
    if str(proj["owner_id"]) == str(user["_id"]):
        role = "admin"
    elif user.get("role") == "admin":
        role = "admin"
    else:
        share = await db.shares.find_one({"project_id": str(proj["_id"]), "user_id": str(user["_id"])})
        if share:
            role = share.get("role", "viewer")
    if role is None:
        raise HTTPException(status_code=403, detail="You do not have access to this project")
    if write and role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Viewer role cannot modify this project")
    out = dict(proj)
    out["_access_role"] = role
    return out


async def log_activity(project_id: str, user: dict, action: str, detail: str = ""):
    await db.activity.insert_one({
        "project_id": project_id, "user_id": str(user["_id"]),
        "user_name": user.get("name") or user.get("email"),
        "action": action, "detail": detail, "at": now_iso(),
    })


# ---------------------------------------------------------------- projects
@api.get("/projects")
async def list_projects(user: dict = Depends(get_current_user)):
    shared = await db.shares.find({"user_id": str(user["_id"])}).to_list(500)
    shared_ids = [oid(s["project_id"]) for s in shared]
    query = {} if user.get("role") == "admin" else {
        "$or": [{"owner_id": str(user["_id"])}, {"_id": {"$in": shared_ids}}]}
    docs = await db.projects.find(query).sort("updated_at", -1).to_list(200)
    out = []
    for d in docs:
        a = engine.analyse(d)
        out.append({
            "id": str(d["_id"]), "name": d.get("name"), "client": d.get("client"),
            "location": d.get("location"), "status": d.get("status", "draft"),
            "plot_reference": d.get("plot_reference"),
            "updated_at": d.get("updated_at"), "created_at": d.get("created_at"),
            "owner_id": d.get("owner_id"),
            "public_token": d.get("public_token"),
            "shared": str(d["owner_id"]) != str(user["_id"]),
            "summary": {
                "plot_area_sqm": a["areas"]["plot_area_sqm"],
                "builtup_area_sqm": a["areas"]["builtup_area_sqm"],
                "total_units": a["areas"]["total_units"],
                "towers": len(d.get("towers") or []),
                "far": a["areas"]["far"],
                "cost_total": a["cost"]["total"],
                "compliance_score": a["compliance"]["score"],
            },
        })
    return out


@api.post("/projects")
async def create_project(body: ProjectIn, user: dict = Depends(get_current_user)):
    if user.get("role") == "viewer":
        raise HTTPException(status_code=403, detail="Viewer role cannot create projects")
    doc = default_project(body.name, body.client, body.location, body.plot_reference, str(user["_id"]),
                          latitude=body.latitude, longitude=body.longitude)
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    doc["rev"] = 0
    res = await db.projects.insert_one(doc)
    await log_activity(str(res.inserted_id), user, "project.created", body.name)
    doc["_id"] = res.inserted_id
    return serialize_project(doc)


@api.get("/projects/{project_id}")
async def get_project(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    role = proj.pop("_access_role")
    return {**serialize_project(proj), "access_role": role}


@api.put("/projects/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch, user: dict = Depends(get_current_user)):
    current_project = await load_project(project_id, user, write=True)
    allowed = {"name", "client", "location", "plot_reference", "status", "plot", "towers", "parking", "residential_policy",
               "config", "quantity_ratios", "rates", "labour_rates", "equipment_rates",
               "utility_config", "compliance_rules", "gis", "engineering", "society_amenities",
               # The programme config carries the user's per-task edits, so it has to be
               # saved with the project: a snapshot must reproduce the programme the user
               # actually approved, not the generated one underneath it.
               "schedule", "finance", "solar", "cost_adders", "wastage_pct",
               # Setbacks live under dev_controls and are the one stored value the site
               # envelope is built from. It was in neither this set nor the frontend's
               # editable list, so every setback edit was silently discarded on reload.
               "dev_controls",
               # The packed layout the 3D view and the site map both render. Same story:
               # absent from this set, so it was regenerated from scratch every session
               # and the freshness stamp it carries could never be checked against
               # anything, because nothing was ever stored to check.
               "site_layout"}
    updates = {k: v for k, v in body.updates.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    validate_project({**current_project, **updates})
    if "towers" in updates or "residential_policy" in updates:
        towers = updates.get("towers", current_project.get("towers") or [])
        policy = updates.get("residential_policy") or current_project.get("residential_policy")
        if policy and isinstance(towers, list) and all(isinstance(t, dict) for t in towers):
            towers = [dict(t) for t in towers]
            for tower in towers:
                unit_count = sum(max(0, int(u.get("count") or 0)) for u in (tower.get("units") or []))
                if unit_count:
                    tower["units_per_floor"] = unit_count
                if (tower.get("parking") or {}).get("basement_only"):
                    update_tower_parking(tower, policy)
            updates["towers"] = towers
            has_only_supported_units = all(
                unit_key(u.get("type"))
                for tower in towers for u in (tower.get("units") or [])
            )
            unit_mix = summarize_units(towers) if has_only_supported_units else []
            if unit_mix:
                updates["unit_mix"] = unit_mix
            if towers and all((t.get("parking") or {}).get("basement_only") for t in towers):
                prior_parking = dict(updates.get("parking") or current_project.get("parking") or {})
                derived_parking = parking_summary(towers, prior_parking.get("basement_levels") or 2)
                if "parking" in updates:
                    for key in ("tower_allocations", "reserved_car_spaces", "reserved_bike_spaces",
                                "optional_car_pool_capacity", "slots_required", "slots_provided"):
                        prior_parking[key] = derived_parking[key]
                    updates["parking"] = prior_parking
                else:
                    updates["parking"] = {**prior_parking, **derived_parking}
    updates["updated_at"] = now_iso()

    # Optimistic concurrency.
    #
    # The values above are whole subtrees — `plot`, `towers`, `compliance_rules` — so the
    # last writer used to replace the other's entire tower list, not just the field they
    # touched. Two tabs, or two engineers on a shared project, silently overwrote each
    # other. The filter now includes the revision the client started from, so a write onto
    # a document that moved underneath it matches nothing and is reported instead.
    query: Dict[str, Any] = {"_id": oid(project_id)}
    if body.rev is not None:
        query["rev"] = body.rev
    res = await db.projects.update_one(query, {"$set": updates, "$inc": {"rev": 1}})
    if res.matched_count == 0:
        current = await db.projects.find_one({"_id": oid(project_id)})
        if current is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raise HTTPException(
            status_code=409,
            detail=f"This project was changed by someone else while you were editing "
                   f"(you had revision {body.rev}, it is now {current.get('rev', 0)}). "
                   f"Saving was paused; reload the server version before reapplying your change.")

    await log_activity(project_id, user, "project.updated",
                       body.note or ", ".join(k for k in updates if k != "updated_at"))
    proj = await db.projects.find_one({"_id": oid(project_id)})
    return serialize_project(proj)


@api.delete("/projects/{project_id}")
async def delete_project(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    if str(proj["owner_id"]) != str(user["_id"]) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the owner or an admin can delete a project")
    await db.projects.delete_one({"_id": oid(project_id)})
    await db.versions.delete_many({"project_id": project_id})
    await db.shares.delete_many({"project_id": project_id})
    return {"ok": True}


# ---------------------------------------------------------------- public compliance link
@api.post("/projects/{project_id}/public-link")
async def create_public_link(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    token = secrets.token_urlsafe(16)
    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$set": {"public_token": token, "updated_at": now_iso()}})
    await log_activity(project_id, user, "public_link.created", "compliance share link")
    return {"public_token": token}


@api.delete("/projects/{project_id}/public-link")
async def revoke_public_link(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    await db.projects.update_one({"_id": oid(project_id)}, {"$unset": {"public_token": ""}})
    await log_activity(project_id, user, "public_link.revoked", "compliance share link")
    return {"ok": True}


@api.get("/public/compliance/{token}")
async def public_compliance(token: str):
    proj = await db.projects.find_one({"public_token": token})
    if not proj:
        raise HTTPException(status_code=404, detail="This link is invalid or has been revoked")
    a = engine.analyse(proj)
    ar = a["areas"]
    return {
        "project": {"name": proj.get("name"), "client": proj.get("client"),
                    "location": proj.get("location"), "plot_reference": proj.get("plot_reference"),
                    "status": proj.get("status", "draft"), "updated_at": proj.get("updated_at")},
        "metrics": {"plot_area_sqm": ar["plot_area_sqm"], "plot_area_acres": ar["plot_area_acres"],
                    "builtup_area_sqm": ar["builtup_area_sqm"], "far": ar["far"], "fsi": ar["fsi"],
                    "ground_coverage_pct": ar["ground_coverage_pct"], "open_space_pct": ar["open_space_pct"],
                    "total_units": ar["total_units"], "towers": len(proj.get("towers") or []),
                    "max_height_m": ar["max_height_m"]},
        "compliance": a["compliance"],
    }


# ---------------------------------------------------------------- scheme comparison
def _scheme_geometry(doc, an):
    """Footprint rectangles and tower positions, for comparing two schemes by shape.

    Scalar metrics cannot tell two schemes apart when one is four squat towers and the
    other is two slender ones on the same FAR. This returns only what is needed to draw
    them side by side -- a bounding box and one rectangle per tower, in plot-local metres
    -- never the full polygon vertex arrays.
    """
    plot = doc.get("plot") or {}
    area = float(an["areas"]["plot_area_sqm"] or 0)
    length = float(plot.get("length") or 0)
    width = float(plot.get("width") or 0)

    # The recorded length x width often disagrees with the drawn polygon's area -- the
    # sample project is 80 x 50 against a 15,219 m2 polygon. Drawing the box anyway would
    # put the towers on 4,000 m2 and make the coverage look four times what it is, so the
    # box is only trusted when it roughly agrees with the area it claims to enclose.
    box_ok = length > 0 and width > 0 and area > 0 and abs(length * width - area) / area < 0.15
    if box_ok:
        basis = "recorded plot dimensions"
    elif area > 0:
        # Keep the drawn aspect ratio where there is one, but scale it to the real area.
        ratio = (length / width) if (length > 0 and width > 0) else 1.0
        width = math.sqrt(area / ratio)
        length = width * ratio
        basis = "scaled to the drawn polygon area"
    else:
        length = width = 0.0
        basis = "no plot geometry"

    towers = []
    positioned = 0
    for t, tm in zip(doc.get("towers") or [], an["areas"]["towers"]):
        fp = float(tm.get("footprint_sqm") or 0)
        # Towers carry an area, not a shape; assume the square that area implies, which is
        # what the massing tools already do.
        side = math.sqrt(fp) if fp > 0 else 0
        pos = t.get("position") or {}
        px, py = float(pos.get("x") or 0), float(pos.get("y") or 0)
        if px or py:
            positioned += 1
        towers.append({
            "id": tm.get("id"), "name": tm.get("name"),
            "x": round(px, 2), "y": round(py, 2),
            "w": round(side, 2), "d": round(side, 2),
            "rotation_deg": round(float(t.get("rotation_deg") or 0), 1),
            "floors": tm.get("floors"), "height_m": tm.get("height_m"),
            "footprint_sqm": round(fp, 1),
        })

    # Without stored positions every tower sits at the origin, which draws them stacked on
    # top of each other. Spread them along the plot instead and mark the layout indicative,
    # so the comparison shows massing rather than a single misleading square.
    placed = "as positioned"
    if towers and positioned == 0:
        gap = 4.0
        run = sum(t["w"] for t in towers) + gap * (len(towers) - 1)
        cursor = max((length - run) / 2, 0.0)
        for t in towers:
            t["x"] = round(cursor, 2)
            t["y"] = round(max((width - t["d"]) / 2, 0.0), 2)
            cursor += t["w"] + gap
        placed = "indicative -- no tower positions recorded"

    return {
        "plot": {"length_m": round(length, 2), "width_m": round(width, 2),
                 "area_sqm": area, "basis": basis,
                 "orientation_deg": float(plot.get("orientation_deg") or 0)},
        "towers": towers, "placement": placed,
        "ground_coverage_pct": an["areas"]["ground_coverage_pct"],
        "open_space_pct": an["areas"]["open_space_pct"],
    }


@api.get("/projects/{project_id}/versions/compare")
async def compare_versions(project_id: str, a: str = "", b: str = "",
                           user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)

    async def scheme(vid):
        if vid == "current":
            return {"id": "current", "label": "Current project", "at": proj.get("updated_at"),
                    "doc": proj}
        v = await db.versions.find_one({"_id": oid(vid), "project_id": project_id})
        if not v:
            raise HTTPException(status_code=404, detail="Version not found")
        return {"id": vid, "label": v["label"], "at": v["at"], "doc": v["snapshot"]}

    out = []
    for vid in (a, b):
        if not vid:
            raise HTTPException(status_code=400, detail="Pick two schemes to compare")
        s = await scheme(vid)
        an = engine.analyse(s["doc"])
        ar, co, pk = an["areas"], an["compliance"], an["parking"]
        util = an["utilities"]

        # Sustainability and money are what a scheme is actually chosen on, and neither
        # was comparable before: two layouts with identical areas can differ by hundreds
        # of tonnes of carbon and several points of margin.
        try:
            eng = englib.analyse_engineering(s["doc"], an)
            green = eng["modules"]["green"]
            carbon = eng["modules"]["carbon"]
            green_score = next((o["value"] for o in green["outputs"] if o["label"] == "Score"), None)
            carbon_per_sqm = carbon["derived"]["per_sqm_kg"]
            carbon_total = carbon["derived"]["total_tco2e"]
            trees = eng["summary"].get("trees_required")
        except Exception:            # a scheme too incomplete to engineer still compares
            green_score = carbon_per_sqm = carbon_total = trees = None

        # Water efficiency: how much of the yearly demand rainwater harvesting can meet.
        demand_yr = float(util.get("water_demand_lpd") or 0) * 365.0
        rwh_yr = float(util.get("rwh_annual_litres") or 0)
        water_eff = round(rwh_yr / demand_yr * 100, 1) if demand_yr else None

        try:
            fin = financelib.analyse(s["doc"], an, s["doc"].get("finance"))
            roi, margin = fin["profit"]["roi_pct"], fin["profit"]["margin_pct"]
            irr, payback = fin["profit"]["irr_pct"], fin["timing"]["payback_month"]
            revenue = fin["revenue"]["gross"]
        except Exception:
            roi = margin = irr = payback = revenue = None

        out.append({
            "id": s["id"], "label": s["label"], "at": s["at"],
            "metrics": {
                "Plot area (m²)": ar["plot_area_sqm"], "Built-up area (m²)": ar["builtup_area_sqm"],
                "Carpet area (m²)": ar["carpet_area_sqm"], "FAR": ar["far"], "FSI": ar["fsi"],
                "Ground coverage (%)": ar["ground_coverage_pct"], "Open space (%)": ar["open_space_pct"],
                "Towers": len(s["doc"].get("towers") or []), "Total units": ar["total_units"],
                "Max height (m)": ar["max_height_m"], "Density (units/acre)": ar["density_units_per_acre"],
                "Parking required": pk["required_slots"], "Parking provided": pk["provided_slots"],
                "Total cost (INR)": an["cost"]["total"], "Cost per flat (INR)": an["cost"]["per_unit"],
                "Cost per m² (INR)": an["cost"]["per_sqm"],
                "Compliance passed": f"{co['passed']}/{co['total']}", "Compliance score (%)": co["score"],
                "Green score (%)": green_score,
                "Embodied carbon (tCO₂e)": carbon_total,
                "Carbon per m² (kgCO₂e)": carbon_per_sqm,
                "Water met by rainwater (%)": water_eff,
                "Trees required": trees,
                "Gross revenue (INR)": revenue,
                "Return on cost (%)": roi,
                "Profit margin (%)": margin,
                "Annual IRR (%)": irr,
                "Cash positive (month)": payback,
            },
            "geometry": _scheme_geometry(s["doc"], an),
        })
    keys = list(out[0]["metrics"].keys())
    return {"schemes": out, "keys": keys, "currency": "INR"}


@api.post("/projects/{project_id}/towers")
async def add_tower(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    towers = proj.get("towers") or []
    policy = proj.get("residential_policy") or new_residential_policy()
    next_index = max((tower_index(t.get("name"), i) for i, t in enumerate(towers)), default=-1) + 1
    next_name = f"Tower {chr(65 + next_index)}" if next_index < 26 else f"Tower {next_index + 1}"
    tower = default_tower(next_name, index=next_index, policy=policy)
    towers.append(tower)
    updates = {"towers": towers, "updated_at": now_iso()}
    updates["unit_mix"] = summarize_units(towers)
    if all(t.get("parking") for t in towers):
        old_parking = dict(proj.get("parking") or {})
        updates["parking"] = {**old_parking, **parking_summary(towers, old_parking.get("basement_levels") or 2)}
    if not proj.get("residential_policy"):
        updates["residential_policy"] = policy
    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$inc": {"rev": 1}, "$set": updates})
    await log_activity(project_id, user, "tower.added", tower["name"])
    fresh = await db.projects.find_one({"_id": oid(project_id)}, {"rev": 1})
    return {"tower": tower, "towers": towers, "rev": (fresh or {}).get("rev")}


class SyncTowersIn(BaseModel):
    # The layout the caller just computed. Optional: without it the stored one is used.
    # It is accepted because the modules that generate a layout hold it locally for a
    # debounce before autosave writes it, and syncing off the stale stored copy would
    # rebuild the towers from the previous run.
    site_layout: Optional[Dict[str, Any]] = None


@api.post("/projects/{project_id}/towers/sync-from-layout")
async def sync_towers_from_layout(project_id: str, body: SyncTowersIn = SyncTowersIn(),
                                  user: dict = Depends(get_current_user)):
    """Rebuild the project's towers from the site layout engine's packed blocks.

    The engine already decides how many buildings fit inside the setback envelope and how
    many floors each carries; this makes Apartment Planning and the 3D model read those
    numbers instead of a hand-kept list that drifts away from them.
    """
    proj = await load_project(project_id, user, write=True)
    layout = body.site_layout or proj.get("site_layout") or {}
    engine_towers = layout.get("towers") or []
    if not engine_towers:
        raise HTTPException(status_code=400,
                            detail="No site layout to sync from. Generate the layout in "
                                   "Plot & Setbacks first.")

    before = proj.get("towers") or []
    policy = proj.get("residential_policy") or new_residential_policy()
    towers = towers_from_site_layout(before, engine_towers, policy)
    updates = {"towers": towers, "updated_at": now_iso()}
    updates["residential_policy"] = policy
    updates["unit_mix"] = summarize_units(towers)
    if all((t.get("parking") or {}).get("basement_only") for t in towers):
        old_parking = dict(proj.get("parking") or {})
        updates["parking"] = {**old_parking, **parking_summary(towers, old_parking.get("basement_levels") or 2)}
    if body.site_layout:
        updates["site_layout"] = body.site_layout
    await db.projects.update_one({"_id": oid(project_id)}, {"$inc": {"rev": 1}, "$set": updates})
    await log_activity(project_id, user, "towers.synced_from_layout",
                       f"{len(towers)} tower(s) from site layout")
    fresh = await db.projects.find_one({"_id": oid(project_id)}, {"rev": 1})
    return {
        "towers": towers,
        "tower_count": len(towers),
        "added": max(len(towers) - len(before), 0),
        "removed": max(len(before) - len(towers), 0),
        "floors": [t["floors"] for t in towers],
        "rev": (fresh or {}).get("rev"),
    }


# ---------------------------------------------------------------- per-floor room layout
class FloorLayoutIn(BaseModel):
    floor: int
    regenerate: bool = False
    use_ai: bool = False
    units: Optional[List[Dict[str, Any]]] = None


@api.post("/projects/{project_id}/towers/{tower_id}/floor-layout")
async def floor_layout(project_id: str, tower_id: str, body: FloorLayoutIn,
                       user: dict = Depends(get_current_user)):
    """Fetch (generating on first visit) or explicitly regenerate one floor's room layout.
    Existing layouts are never silently overwritten — only `regenerate: true` reseeds a
    floor, so hand-edited rooms survive normal navigation between floors/towers."""
    if body.floor < 1:
        raise HTTPException(status_code=400, detail="Floor must be 1 or higher")
    proj = await load_project(project_id, user, write=True)
    towers = proj.get("towers") or []
    tower = next((t for t in towers if t.get("id") == tower_id), None)
    if not tower:
        raise HTTPException(status_code=404, detail="Tower not found")

    if body.units is not None:
        tower["units"] = body.units

    tower.setdefault("floor_layouts", {})
    key = str(body.floor)
    existing = tower["floor_layouts"].get(key)
    current_hash = layoutlib.unit_mix_hash(tower)

    if existing and not body.regenerate and not body.use_ai:
        return {"tower": tower, "towers": towers, "rooms": existing["rooms"],
                "validation": existing.get("validation") or {},
                "stale": (existing.get("unit_mix_hash") != current_hash
                          or (not existing.get("ai_generated")
                              and int(existing.get("planner_version") or 1) < aifloorplan.PLANNER_VERSION))}

    if body.use_ai:
        rooms, validation = await aifloorplan.generate_ai_floor_layout(tower, body.floor)
        entry = {
            "rooms": rooms,
            "validation": validation,
            "seed": 888,
            "unit_mix_hash": current_hash,
            "generated_at": now_iso(),
            "ai_generated": True,
        }
    else:
        nonce = (int(existing.get("seed", -1)) + 1) if (existing and body.regenerate) else 0
        entry = floor_layout_entry(tower, body.floor, nonce)
        vastu_audit = aifloorplan.audit_vastu_and_mep(entry["rooms"], body.floor, max(int(tower.get("floors") or 1), 1))
        entry["validation"] = {**(entry.get("validation") or {}), "vastu": vastu_audit}

    tower["floor_layouts"][key] = entry
    if body.floor == 1:
        tower["rooms"] = entry["rooms"]

    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$inc": {"rev": 1}, "$set": {"towers": towers, "updated_at": now_iso()}})
    fresh = await db.projects.find_one({"_id": oid(project_id)}, {"rev": 1})
    await log_activity(project_id, user, "tower.floor_layout_generated",
                       f"{tower.get('name')} · floor {body.floor}{' (AI)' if body.use_ai else ''}")
    return {"tower": tower, "towers": towers, "rooms": entry["rooms"],
            "validation": entry.get("validation") or {}, "stale": False, "ai_generated": body.use_ai,
            "rev": (fresh or {}).get("rev")}


@api.post("/projects/{project_id}/towers/{tower_id}/ai-floor-layout")
async def ai_floor_layout(project_id: str, tower_id: str, body: FloorLayoutIn,
                          user: dict = Depends(get_current_user)):
    """Explicitly generate an AI-architected residential floor layout for a tower."""
    body.use_ai = True
    body.regenerate = True
    return await floor_layout(project_id, tower_id, body, user=user)


class GenerateAllFloorsIn(BaseModel):
    use_ai: bool = False
    units: Optional[List[Dict[str, Any]]] = None


@api.post("/projects/{project_id}/towers/{tower_id}/generate-all-floors")
async def generate_all_floors(project_id: str, tower_id: str, body: GenerateAllFloorsIn = GenerateAllFloorsIn(),
                              user: dict = Depends(get_current_user)):
    """Generate dynamic Vastu-compliant architectural floor layouts for every floor of the tower."""
    proj = await load_project(project_id, user, write=True)
    towers = proj.get("towers") or []
    tower = next((t for t in towers if t.get("id") == tower_id), None)
    if not tower:
        raise HTTPException(status_code=404, detail="Tower not found")

    if body.units is not None:
        tower["units"] = body.units

    floors = max(int(tower.get("floors") or 1), 1)
    tower.setdefault("floor_layouts", {})
    current_hash = layoutlib.unit_mix_hash(tower)

    for fl in range(1, floors + 1):
        key = str(fl)
        if body.use_ai:
            rooms, validation = await aifloorplan.generate_ai_floor_layout(tower, fl)
            entry = {
                "rooms": rooms,
                "validation": validation,
                "seed": 888 + fl,
                "unit_mix_hash": current_hash,
                "generated_at": now_iso(),
                "ai_generated": True,
            }
        else:
            rooms, validation = aifloorplan.generate_architectural_template(tower, fl)
            entry = {
                "rooms": rooms,
                "validation": validation,
                "seed": fl,
                "unit_mix_hash": current_hash,
                "generated_at": now_iso(),
                "ai_generated": False,
            }
        tower["floor_layouts"][key] = entry
        if fl == 1:
            tower["rooms"] = entry["rooms"]

    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$inc": {"rev": 1}, "$set": {"towers": towers, "updated_at": now_iso()}})
    fresh = await db.projects.find_one({"_id": oid(project_id)}, {"rev": 1})
    await log_activity(project_id, user, "tower.all_floors_generated",
                       f"{tower.get('name')} · all {floors} floors{' (AI)' if body.use_ai else ''}")
    return {"tower": tower, "towers": towers, "floors_generated": floors,
            "rev": (fresh or {}).get("rev")}


@api.get("/projects/{project_id}/analysis")
async def project_analysis(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(engine.analyse, proj)


class AnalyseIn(BaseModel):
    project: Dict[str, Any]


class GisIn(BaseModel):
    radius_m: int = 500


@api.post("/analyse")
async def analyse_live(body: AnalyseIn, user: dict = Depends(get_current_user)):
    """Stateless calculation endpoint for live editing before save."""
    return await run_in_threadpool(engine.analyse, body.project)


# ---------------------------------------------------------------- IS/NBC engineering modules
@api.post("/engineering/analyse")
async def engineering_live(body: AnalyseIn, user: dict = Depends(get_current_user)):
    """Stateless IS/NBC module calculations for live editing."""
    base = await run_in_threadpool(engine.analyse, body.project)
    return await run_in_threadpool(englib.analyse_engineering, body.project, base)


@api.get("/projects/{project_id}/engineering")
async def engineering_for_project(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    base = await run_in_threadpool(engine.analyse, proj)
    return await run_in_threadpool(englib.analyse_engineering, proj, base)


# ---------------------------------------------------------------- site layout engine
class SiteLayoutIn(BaseModel):
    """Partial config override; anything omitted falls back to SiteLayoutConfig defaults."""
    config: Dict[str, Any] = Field(default_factory=dict)


class SiteLayoutLiveIn(SiteLayoutIn):
    project: Dict[str, Any]


@api.post("/projects/{project_id}/site-layout/envelope")
async def site_layout_envelope(project_id: str, body: SiteLayoutIn,
                               user: dict = Depends(get_current_user)):
    """Stage 1 — buildable envelope for a saved project."""
    proj = await load_project(project_id, user)
    return await run_in_threadpool(siteplanlib.buildable_envelope, proj, body.config)


@api.post("/site-layout/envelope")
async def site_layout_envelope_live(body: SiteLayoutLiveIn,
                                    user: dict = Depends(get_current_user)):
    """Stateless envelope for live editing before save — mirrors /analyse."""
    return await run_in_threadpool(siteplanlib.buildable_envelope, body.project, body.config)


@api.post("/projects/{project_id}/site-layout/reserve")
async def site_layout_reserve(project_id: str, body: SiteLayoutIn,
                              user: dict = Depends(get_current_user)):
    """Stage 2 — envelope plus reserved roads, amenities and the residual packable region."""
    proj = await load_project(project_id, user)
    return await run_in_threadpool(siteplanlib.reserve_site, proj, body.config)


@api.post("/site-layout/reserve")
async def site_layout_reserve_live(body: SiteLayoutLiveIn,
                                   user: dict = Depends(get_current_user)):
    """Stateless reservation for live editing before save."""
    return await run_in_threadpool(siteplanlib.reserve_site, body.project, body.config)


@api.post("/projects/{project_id}/site-layout/plan")
async def site_layout_plan(project_id: str, body: SiteLayoutIn,
                           user: dict = Depends(get_current_user)):
    """Stage 3 — full layout: envelope, reservation and packed towers."""
    proj = await load_project(project_id, user)
    return await run_in_threadpool(siteplanlib.plan_site, proj, body.config)


@api.post("/site-layout/plan")
async def site_layout_plan_live(body: SiteLayoutLiveIn,
                                user: dict = Depends(get_current_user)):
    """Stateless full layout for live editing before save."""
    return await run_in_threadpool(siteplanlib.plan_site, body.project, body.config)


class GeneticRefineIn(BaseModel):
    generations: int = 80
    population: int = 60
    time_budget_s: float = 3.0
    # Same partial config override the plan stage takes, so refinement searches inside
    # the setbacks, road reservation and tower caps the user actually laid out — not a
    # default plot they never configured.
    config: Dict[str, Any] = Field(default_factory=dict)


class GeneticRefineLiveIn(GeneticRefineIn):
    project: Dict[str, Any]


def _refine_cfg(body: GeneticRefineIn) -> Dict[str, Any]:
    """Config override for a refinement run: the caller's layout config with the GA forced on."""
    cfg = dict(body.config or {})
    ga = dict(cfg.get("ga") or {})
    ga.update({
        "enabled": True,
        "generations": body.generations,
        "population": body.population,
        "time_budget_s": body.time_budget_s,
    })
    cfg["ga"] = ga
    cfg["fast_preview"] = False
    return cfg


@api.post("/projects/{project_id}/site-layout/refine")
async def site_layout_refine(project_id: str, body: GeneticRefineIn = GeneticRefineIn(),
                             user: dict = Depends(get_current_user)):
    """Stage 3b — Genetic refinement of tower placement."""
    proj = await load_project(project_id, user)
    cfg = _refine_cfg(body)
    return await run_in_threadpool(siteplanlib.plan_site, proj, cfg)


@api.post("/site-layout/refine")
async def site_layout_refine_live(body: GeneticRefineLiveIn,
                                  user: dict = Depends(get_current_user)):
    """Stateless stage 3b Genetic refinement of tower placement."""
    cfg = _refine_cfg(body)
    return await run_in_threadpool(siteplanlib.plan_site, body.project, cfg)


# What PlotModule used to hold in local state. Kept as the seed so a project saved before
# setbacks were stored behaves exactly as it did.
DEFAULT_SETBACKS = {"default": 6.0, "front": 9.0, "rear": 4.5, "side": 4.5}


class RecommendIn(BaseModel):
    plot_area_sqm: float
    road_width_m: float = 0.0
    city: str = ""
    state: str = ""
    floor_height: float = 3.0
    area_per_unit: float = 95.0
    far_override: Optional[float] = None


@api.post("/site-layout/recommend")
async def site_layout_recommend(body: RecommendIn, user: dict = Depends(get_current_user)):
    """Recommend setbacks, height, floors and unit yield from the plot and its frontage."""
    return siteplanlib.recommend_controls(
        plot_area=body.plot_area_sqm, road_width=body.road_width_m,
        city=body.city, state=body.state, floor_height=body.floor_height,
        area_per_unit=body.area_per_unit, far_override=body.far_override)


@api.get("/projects/{project_id}/setbacks")
async def project_setbacks(project_id: str, user: dict = Depends(get_current_user)):
    """The applied setbacks, the statutory minimum for each edge, and whether they clear it.

    One endpoint so the module that edits setbacks and the engine that builds the envelope
    cannot disagree about what the minimum is.
    """
    proj = await load_project(project_id, user)
    an = engine.analyse(proj)
    applied = ((proj.get("dev_controls") or {}).get("setbacks")
               or DEFAULT_SETBACKS)
    plot = proj.get("plot") or {}
    edges = plot.get("road_edges") or []
    road_width = max([float(e.get("width") or 0) for e in edges] or [0.0])
    check = siteplanlib.validate_setbacks(
        applied, plot_area=float(an["areas"]["plot_area_sqm"] or 0),
        road_width=road_width, height_m=float(an["areas"]["max_height_m"] or 0))
    return {**check, "applied": applied, "road_width_m": road_width,
            "height_m": an["areas"]["max_height_m"],
            "plot_area_sqm": an["areas"]["plot_area_sqm"]}


@api.get("/site-layout/defaults")
async def site_layout_defaults():
    return {"config": siteplanlib.SiteLayoutConfig().to_dict()}


@api.get("/iscodes")
async def code_library(q: str = "", id: str = ""):
    term = (q or "").lower().strip()
    entries = iscodes.CODE_LIBRARY
    if id:
        entries = [c for c in entries if c["id"] == id]
    elif term:
        entries = [c for c in entries if term in c["code"].lower() or term in c["topic"].lower()
                   or term in c["key_value"].lower() or term in c["clause"].lower()
                   or term in c.get("keywords", "")]
    return {"entries": entries, "count": len(entries), "query": q}


@api.get("/cities")
async def city_list(q: str = ""):
    term = (q or "").lower().strip()
    items = [{"city": name, "state": v[0], "zone": v[1], "wind_speed": v[2],
              "annual_rainfall_mm": v[3], "rain_intensity_mm_hr": v[4]}
             for name, v in sorted(iscodes.CITIES.items())]
    if term:
        items = [i for i in items if term in i["city"].lower() or term in i["state"].lower()]
    return {"cities": items, "count": len(items),
            "soils": [{"key": k, **v} for k, v in iscodes.SOILS.items()],
            "exposures": list(iscodes.EXPOSURE.keys()),
            "structural_systems": list(iscodes.RESPONSE_R.keys()),
            "green_checklist": iscodes.GREEN_CHECKLIST}


# ---------------------------------------------------------------- clause retrieval
# /iscodes above serves the 20-entry curated library: a code, a topic and a headline value.
# These three serve the clause TEXT, out of whatever corpus the operator loaded. Nothing
# here ships with the app, so all three have to read the same on an empty install as on a
# full one -- the feature has nothing to say, and says which.
#
# codesearch is synchronous throughout: `search` may make a blocking embedding call for
# the query, and the first call of either kind reads the index off disk. So the calls below
# go through run_in_threadpool -- inline in an async handler they would stall the event
# loop for the whole process, not just the request that asked.
#
# Authenticated, unlike /iscodes. The corpus is the operator's own licensed documents and
# the manifest names their files; /ai/status is behind the same door for the same reason.

class CodeAskIn(BaseModel):
    question: str
    project_id: Optional[str] = None
    code_id: Optional[str] = None


ASK_HITS = 6              # passages the answer may quote from
SEARCH_HITS = 5           # default for the raw retrieval route
SEARCH_HITS_MAX = 25      # a debugging route, not a bulk export of the corpus


def _source(hit: Dict[str, Any], scores: bool = False) -> Dict[str, Any]:
    """A retrieval hit as the reader sees it: enough to quote the clause and find it again.

    The component scores are debugging output, so they travel only on the route that
    exists to debug retrieval.
    """
    row = {k: hit.get(k) for k in ("chunk_id", "code", "clause", "heading", "text")}
    row["score"] = hit.get("score")
    if scores:
        row.update({k: hit.get(k)
                    for k in ("vector_score", "keyword_score", "designation_score")})
    return row


@api.post("/codes/ask")
async def codes_ask(body: CodeAskIn, user: dict = Depends(get_current_user)):
    """Answer a code question from the retrieved clause text -- or refuse to answer it."""
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question")

    hits = await run_in_threadpool(codesearchlib.search, question, ASK_HITS, body.code_id)
    if not hits:
        # The refusal is the feature, not a degraded path around it. Falling through to an
        # unretrieved model answer would return exactly the fluent invented clause the
        # corpus exists to replace, and the reader would have no way to tell which of the
        # two they were given.
        return {"answered": False,
                "reason": "no matching clause in the loaded code corpus",
                "sources": [], "answer": "",
                "verified_citations": [], "unverified_citations": [],
                "degraded": bool(codesearchlib.index_status()["degraded"])}

    sources = [_source(h) for h in hits]
    # The model is given the same rows the caller gets back, so every statement in the
    # answer can be checked against the text it was drawn from.
    context: Dict[str, Any] = {"question": question, "extracts": sources}
    if body.project_id:
        # Through load_project, so a project the user cannot see 404s or 403s here exactly
        # as it does everywhere else. Read access is enough: asking a question stores
        # nothing.
        proj = await load_project(body.project_id, user)
        an, eng = speedlib.cached_analysis(proj, engine.analyse, englib.analyse_engineering)
        context["project_state"] = aptlib.build(proj, an, eng)

    try:
        result = await ailib.generate_markdown(
            ailib.PROMPTS["codes"],
            "Use only the extracts below.\n\n" + ailib.context_block(context),
            session_hint=f"codes-{body.project_id or 'library'}")
    except ailib.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ailib.AIFailed as exc:
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {exc}")

    cites = citelib.verify_with_extracts(result["text"], sources)
    return {"answered": True, "answer": result["text"], "sources": sources,
            "verified_citations": cites["resolved"],
            "unverified_citations": cites["unverified"],
            "unconfirmed_values": cites.get("unconfirmed_values", []),
            "degraded": bool(hits[0].get("degraded"))}


@api.get("/codes/search")
async def codes_search(q: str = "", code_id: str = "", k: int = SEARCH_HITS,
                       user: dict = Depends(get_current_user)):
    """Retrieval on its own, with no model behind it.

    The library UI reads it, and so does anyone asking why an answer quoted the clause it
    did -- which is why the component scores come back here and nowhere else.
    """
    query = (q or "").strip()
    if not query:
        # An empty box is not an error: the search field calls this as the user types.
        return {"results": [], "count": 0, "query": q, "degraded": False}
    hits = await run_in_threadpool(codesearchlib.search, query,
                                   max(1, min(k, SEARCH_HITS_MAX)), code_id or None)
    return {"results": [_source(h, scores=True) for h in hits], "count": len(hits),
            "query": q, "degraded": bool(hits and hits[0].get("degraded"))}


@api.get("/codes/index")
async def codes_index(user: dict = Depends(get_current_user)):
    """What the index holds and whether it can answer, so the UI can show the feature as
    unavailable WITH the reason rather than failing on a search that returns nothing."""
    return await run_in_threadpool(codesearchlib.index_status)


# ---------------------------------------------------------------- GIS & site intelligence
@api.get("/projects/{project_id}/gis")
async def get_gis(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    stored = proj.get("gis")
    coords = (proj.get("plot") or {}).get("coordinates") or []
    # "polygon" (the boundary moved) or "rules" (the analysis rules changed) or None.
    # The reason is returned, not just a flag, so the UI can say which one applies rather
    # than blaming the boundary for a rule change.
    stale_reason = gislib.staleness(stored, coords)
    return {"gis": stored, "stale": stale_reason is not None, "stale_reason": stale_reason,
            "has_polygon": len(coords) >= 3}


@api.post("/projects/{project_id}/gis/prefetch")
async def prefetch_gis(project_id: str, body: GisIn, user: dict = Depends(get_current_user)):
    """Warm the map-data cache for this site so a later analysis does not wait on the
    public Overpass mirrors. Returns immediately; the download runs in the background."""
    proj = await load_project(project_id, user)
    coords = (proj.get("plot") or {}).get("coordinates") or []
    radius = max(100, min(int(body.radius_m or 500), 2000))
    return {"started": gislib.prefetch_overpass(coords, radius)}


@api.post("/projects/{project_id}/gis/analyse")
async def run_gis(project_id: str, body: GisIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    try:
        result = await gislib.analyse_site(proj, body.radius_m)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$inc": {"rev": 1}, "$set": {"gis": result, "updated_at": now_iso()}})
    await log_activity(project_id, user, "gis.analysed",
                       f"radius {result['radius_m']} m · suitability {result['suitability']['score']}")
    # The write above bumps the revision; the workspace needs it or its next autosave is
    # refused as a conflict with this very analysis.
    rev = (await db.projects.find_one({"_id": oid(project_id)}, {"rev": 1}) or {}).get("rev")
    return {"gis": result, "stale": False, "stale_reason": None, "has_polygon": True, "rev": rev}


# ---------------------------------------------------------------- AI assistance
# Every AI feature shares one shape: build a context dict out of numbers the app has
# ALREADY computed, hand it to ai.generate_markdown with a task-specific system prompt,
# and store the markdown on the project so it survives a reload. The model never
# calculates anything -- it only explains what the engine produced.

async def _run_ai(kind: str, context: dict, *, store_at: str = "", project_id: str = "",
                  user: dict = None, activity: str = "") -> dict:
    """Shared tail of every AI endpoint: call the model, store, log."""
    try:
        result = await ailib.generate_markdown(
            ailib.PROMPTS[kind],
            "Use only the JSON data below.\n\n" + ailib.context_block(context),
            session_hint=f"{kind}-{project_id}",
        )
    except ailib.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ailib.AIFailed as exc:
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {exc}")

    summary = {"text": result["text"], "model": result["model"],
               "provider": result["provider"], "generated_at": now_iso()}
    if store_at and project_id:
        await db.projects.update_one({"_id": oid(project_id)},
                                     {"$inc": {"rev": 1}, "$set": {store_at: summary, "updated_at": now_iso()}})
    if activity and project_id and user:
        await log_activity(project_id, user, activity, f"{result['model']} analysis generated")
    return summary


@api.get("/ai/status")
async def ai_status(user: dict = Depends(get_current_user)):
    """Lets the UI grey out AI buttons (and say why) instead of failing on click."""
    return ailib.provider()


@api.post("/projects/{project_id}/gis/ai-summary")
async def gis_ai_summary(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    stored = proj.get("gis")
    if not stored:
        raise HTTPException(status_code=400, detail="Run the site analysis before generating an AI summary")
    context = gislib.ai_context(proj, stored)
    summary = await _run_ai("gis", context, store_at="gis.ai_summary",
                            project_id=project_id, user=user, activity="gis.ai_summary")
    rev = (await db.projects.find_one({"_id": oid(project_id)}, {"rev": 1}) or {}).get("rev")
    return {**summary, "rev": rev}


@api.post("/projects/{project_id}/ai/compliance")
async def ai_compliance(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    an = engine.analyse(proj)
    comp = an["compliance"]
    context = {
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "score_pct": comp["score"], "passed": comp["passed"], "failed": comp["failed"],
        "total_rules": comp["total"], "overall": comp["overall"],
        "measured_parameters": comp["params"],
        "rules": [{"code": r["code"], "label": r["label"], "param": r["param"],
                   "requirement": f"{r['operator']} {r['threshold']}{r.get('unit') or ''}",
                   "actual": r["actual"], "status": r["status"]} for r in comp["results"]],
        "context_for_fixes": {
            "far": an["areas"]["far"], "ground_coverage_pct": an["areas"]["ground_coverage_pct"],
            "open_space_pct": an["areas"]["open_space_pct"],
            "total_units": an["areas"]["total_units"],
            "parking_required": an["parking"]["required_slots"],
            "parking_provided": an["parking"]["provided_slots"],
            "towers": [{"name": t.get("name"), "floors": t.get("floors")}
                       for t in (proj.get("towers") or [])],
        },
    }
    return await _run_ai("compliance", context, store_at="ai.compliance",
                         project_id=project_id, user=user, activity="ai.compliance")


@api.post("/projects/{project_id}/ai/report")
async def ai_report(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    an = engine.analyse(proj)
    ar, co, pk, cost = an["areas"], an["compliance"], an["parking"], an["cost"]
    context = {
        "project": {"name": proj.get("name"), "client": proj.get("client"),
                    "location": proj.get("location"), "status": proj.get("status")},
        "scale": {"plot_area_sqm": ar["plot_area_sqm"], "plot_area_acres": ar["plot_area_acres"],
                  "builtup_area_sqm": ar["builtup_area_sqm"], "carpet_area_sqm": ar["carpet_area_sqm"],
                  "far": ar["far"], "fsi": ar["fsi"],
                  "ground_coverage_pct": ar["ground_coverage_pct"],
                  "open_space_pct": ar["open_space_pct"], "total_units": ar["total_units"],
                  "max_height_m": ar["max_height_m"],
                  "density_units_per_acre": ar["density_units_per_acre"],
                  "tower_count": len(proj.get("towers") or [])},
        "cost_inr": {"total": cost["total"], "per_unit": cost["per_unit"],
                     "per_sqm": cost["per_sqm"], "material": cost["material"],
                     "labour": cost["labour"], "equipment": cost["equipment"]},
        "parking": {"required": pk["required_slots"], "provided": pk["provided_slots"],
                    "deficit": pk["deficit"]},
        "compliance": {"score_pct": co["score"], "passed": co["passed"], "failed": co["failed"],
                       "failing_rules": [r["label"] for r in co["results"] if r["status"] == "fail"]},
        "utilities": an.get("utilities"),
    }
    return await _run_ai("report", context, store_at="ai.report",
                         project_id=project_id, user=user, activity="ai.report")


@api.post("/projects/{project_id}/ai/cost")
async def ai_cost(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    an = engine.analyse(proj)
    bill = an["boq"]
    context = {
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "scale": {"builtup_area_sqm": an["areas"]["builtup_area_sqm"],
                  "carpet_area_sqm": an["areas"]["carpet_area_sqm"],
                  "total_units": an["areas"]["total_units"]},
        "cost_inr": an["cost"],
        "quantities": an["quantities"],
        "boq": {k: v for k, v in bill.items() if k != "currency"},
        "configured_rates": {"materials": proj.get("rates"), "labour": proj.get("labour_rates"),
                             "equipment": proj.get("equipment_rates")},
        "quantity_ratios": proj.get("quantity_ratios"),
    }
    return await _run_ai("cost", context, store_at="ai.cost",
                         project_id=project_id, user=user, activity="ai.cost")


@api.post("/projects/{project_id}/ai/engineering")
async def ai_engineering(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    eng = englib.analyse_engineering(proj, engine.analyse(proj))
    context = {
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "city_reference": eng.get("city_reference"),
        "summary": eng.get("summary"),
        "modules": {k: {"title": m.get("title"), "outputs": m.get("outputs"),
                        "derived": m.get("derived"),
                        "recommendation": m.get("recommendation"),
                        "code_refs": m.get("code_refs") or m.get("codes")}
                    for k, m in (eng.get("modules") or {}).items()},
        "per_tower": eng.get("per_tower"),
        "missing_inputs": eng.get("missing_inputs"),
        "warnings": eng.get("warnings"),
    }
    return await _run_ai("engineering", context, store_at="ai.engineering",
                         project_id=project_id, user=user, activity="ai.engineering")


@api.get("/projects/{project_id}/ai/compare")
async def ai_compare(project_id: str, a: str = "", b: str = "",
                     user: dict = Depends(get_current_user)):
    """Narrative for a two-scheme comparison. Not stored -- it belongs to the chosen pair,
    not to the project, so caching it on the document would go stale silently."""
    if not a or not b:
        raise HTTPException(status_code=400, detail="Pick two schemes to compare")
    comparison = await compare_versions(project_id, a=a, b=b, user=user)
    schemes = comparison["schemes"]
    keys = comparison["keys"]
    context = {
        "currency": comparison["currency"],
        "scheme_a": {"label": schemes[0]["label"], "saved_at": schemes[0]["at"],
                     "metrics": schemes[0]["metrics"]},
        "scheme_b": {"label": schemes[1]["label"], "saved_at": schemes[1]["at"],
                     "metrics": schemes[1]["metrics"]},
        "metrics_that_differ": [k for k in keys
                                if schemes[0]["metrics"].get(k) != schemes[1]["metrics"].get(k)],
    }
    return await _run_ai("compare", context, project_id=project_id)


# ---------------------------------------------------------------- APT assistant
class ChatIn(BaseModel):
    messages: List[Dict[str, Any]] = Field(default_factory=list)


# Only the last few turns go upstream: a long thread costs more than it adds, and the
# project state -- which is rebuilt fresh every message -- is what actually answers the
# question. The full thread is still stored so the user sees their own history.
CHAT_TURNS_SENT = 12
CHAT_TURNS_STORED = 60

# Fewer extracts than /codes/ask gets: there the clause text is the whole answer, here it
# shares the prompt with the entire project state.
APT_HITS = 4


def _attach_extracts(context: Dict[str, Any],
                     extracts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Retrieved clause text added to a context that was cached without it.

    speedlib.cached_context is keyed on the project's version, and these extracts were
    retrieved for THIS question. Baked into the cached object they would answer every
    later question about the project with whatever clauses the first one happened to
    match, so they are attached after the cache has handed its context over -- and to a
    copy, because mutating the cached dict would poison it for the life of the process.

    aptcontext owns the serialisation and the extract budget and both tiers borrow it, so
    the full and light payloads carry one "code_extracts" shape rather than two -- the
    same one `aptcontext.build(code_extracts=...)` produces for a caller outside the cache.
    """
    if not extracts:
        # Silence here is what makes APT answer a code question from memory: the key is
        # simply absent, the prompt has nothing to say about clause text, and the model
        # fills the gap with a fluent invented clause. Saying WHY there is no extract is
        # what turns that into a refusal.
        status = codesearchlib.index_status()
        if not status.get("available"):
            note = ("No code corpus is loaded, so no clause text was retrieved for this "
                    "question. Answer from the project's own computed values and the "
                    "clause registry only, and say plainly that the clause text is not "
                    "available. Do not quote or paraphrase a clause from memory.")
            return {**context, "code_corpus": {"available": False, "note": note}}
        note = ("The code corpus is loaded but returned no passage above the relevance "
                "floor for this question. Treat that as the corpus not covering it: do "
                "not substitute a remembered clause.")
        return {**context, "code_corpus": {"available": True, "matched": False,
                                           "note": note}}
    return {**context, "code_extracts": aptlib._extracts(extracts)}



_FOLLOWUP_MARKERS = re.compile(
    r"\b(it|its|this|that|these|those|they|them|the same|above|previous|earlier"
    r"|what about|and also|how about|compared to|governs?|limiting)\b", re.IGNORECASE)


async def _rewrite_query(question: str, messages: List[Dict[str, Any]]) -> str:
    """Rewrite a follow-up question into a standalone query for retrieval.

    Only fires when prior turns exist and the question looks like a follow-up
    (pronouns, short phrase, or references prior context). Falls back safely
    to the original question if rewriting is unneeded or fails.
    """
    prior = [m for m in messages[:-1] if m.get("role") in ("user", "assistant")]
    if not prior:
        return question
    words = question.split()
    if len(words) > 7 and not _FOLLOWUP_MARKERS.search(question):
        return question

    recent = prior[-4:]
    context_lines = [f"{m['role'].upper()}: {str(m.get('content', ''))[:150]}" for m in recent]
    prompt = (
        "Conversation:\n" + "\n".join(context_lines) +
        f"\n\nLatest user question: {question}\n\n"
        "Rewrite the latest question into a single standalone civil-engineering search query. "
        "Resolve all pronouns (it, its, this, that) using the conversation context. "
        "Output ONLY the rewritten search query, with no explanation or quotation marks."
    )
    try:
        res = await ailib.generate_markdown(
            "You are a search query rewriter for an Indian civil engineering code retrieval system.",
            prompt,
            session_hint="query-rewrite",
            prefer_fast=True,
            temperature=0.0
        )
        candidate = res.get("text", "").strip().strip('"\'`')
        if candidate and 3 <= len(candidate) <= 300:
            logger.info("apt.rewrite: %r -> %r", question, candidate)
            return candidate
    except Exception as exc:
        logger.debug("Query rewriting skipped: %s", exc)
    return question


_WHATIF_TRIGGERS = re.compile(
    r"\b(what\s+if|what\s+happens\s+if|how\s+does\s+.*\s+change\s+if|suppose\s+we|if\s+we\s+(?:change|increase|decrease|use))\b",
    re.IGNORECASE
)


def _try_whatif(question: str, proj: Dict[str, Any], base: Dict[str, Any],
                eng: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect what-if engineering questions, re-run analysis on modified params,
    and return exact deterministic deltas between baseline and hypothetical state."""
    if not _WHATIF_TRIGGERS.search(question):
        return None

    low = question.lower()
    overrides: Dict[str, Any] = {}

    # Concrete grade (e.g. "M30", "concrete grade 35", "M-40")
    m = re.search(r"\b(?:m\s*(\d{2})|concrete\s*(?:grade)?\s*(?:is|to)?\s*m?\s*(\d{2}))\b", low)
    if m:
        grade = int(m.group(1) or m.group(2))
        if grade in (15, 20, 25, 30, 35, 40, 45, 50, 55, 60):
            overrides["concrete_grade"] = grade

    # Steel grade (e.g. "Fe 500", "Fe550", "steel grade 500")
    m = re.search(r"\b(?:fe\s*(\d{3})|steel\s*(?:grade)?\s*(?:is|to)?\s*(?:fe\s*)?(\d{3}))\b", low)
    if m:
        grade = int(m.group(1) or m.group(2))
        if grade in (250, 415, 500, 550, 600):
            overrides["steel_grade"] = grade

    # Slab thickness (e.g. "slab thickness 150 mm", "150mm slab")
    m = re.search(r"\b(?:(?:slab\s*(?:thickness)?|thickness)\s*(?:is|to|of)?\s*(\d{2,3})\s*mm|(\d{2,3})\s*mm\s*slab)\b", low)
    if m:
        thk = float(m.group(1) or m.group(2))
        if 75 <= thk <= 400:
            overrides["slab_thickness_mm"] = thk

    # Soil type
    m = re.search(r"\b(?:soil\s*(?:type|condition)?\s*(?:is|to)?\s*)(dense sand|medium sand|loose sand|hard rock|soft rock|stiff clay|medium clay|soft clay)\b", low)
    if m:
        overrides["soil_type"] = m.group(1).strip()

    # Exposure condition
    m = re.search(r"\b(?:exposure\s*(?:condition)?\s*(?:is|to)?\s*)(mild|moderate|severe|very severe|extreme)\b", low)
    if m:
        overrides["exposure_condition"] = m.group(1).strip()

    if not overrides:
        return None

    cur_eng = eng.get("config") or {}
    diffs = {k: v for k, v in overrides.items() if cur_eng.get(k) != v}
    if not diffs:
        return None

    try:
        mutated_proj = {**proj, "engineering": {**(proj.get("engineering") or {}), **diffs}}
        mutated_eng = englib.analyse_engineering(mutated_proj, base)

        s0 = eng.get("summary", {})
        s1 = mutated_eng.get("summary", {})
        deltas = {}
        for k in ("base_shear_kn", "embodied_carbon_tco2e", "carbon_per_sqm_kg"):
            v0, v1 = s0.get(k), s1.get(k)
            if v0 is not None and v1 is not None:
                pct = round(((v1 - v0) / v0) * 100, 2) if v0 else 0
                deltas[k] = {"baseline": v0, "hypothetical": v1, "delta": round(v1 - v0, 2), "pct_change": pct}

        for k in ("column_size", "foundation", "mix_ratio"):
            v0, v1 = s0.get(k), s1.get(k)
            if v0 != v1:
                deltas[k] = {"baseline": v0, "hypothetical": v1}

        checks_changed = []
        m0_mods = eng.get("modules", {})
        m1_mods = mutated_eng.get("modules", {})
        for mod_id, mod0 in m0_mods.items():
            mod1 = m1_mods.get(mod_id) or {}
            c0 = {c["label"]: c["status"] for c in mod0.get("checks", []) if "label" in c}
            c1 = {c["label"]: c["status"] for c in mod1.get("checks", []) if "label" in c}
            for lbl, st0 in c0.items():
                st1 = c1.get(lbl)
                if st1 and st0 != st1:
                    checks_changed.append({"check": lbl, "baseline": st0, "hypothetical": st1})

        logger.info("apt.whatif: modified=%s deltas=%d checks_changed=%d",
                    diffs, len(deltas), len(checks_changed))
        return {
            "parameter_changes": diffs,
            "metric_deltas": deltas,
            "status_changes": checks_changed,
        }
    except Exception as exc:
        logger.warning("apt.whatif computation failed: %s", exc)
        return None


@api.post("/projects/{project_id}/ai/chat")
async def ai_chat(project_id: str, body: ChatIn, user: dict = Depends(get_current_user)):
    """APT: answers from this project's live computed state, with citations checked."""
    proj = await load_project(project_id, user, write=True)
    messages = [m for m in body.messages
                if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()]
    if not messages or messages[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="Send at least one user message")

    question = str(messages[-1]["content"])
    tier = speedlib.classify(question)
    timer = speedlib.Timer()

    # Tier `none`: a greeting needs no project data and no model call. This is the whole
    # point of the intent gate -- "hi" used to run fourteen engineering modules, the
    # programme and eleven optimisers before saying hello.
    if tier == speedlib.NONE:
        text = speedlib.reply_for(question)
        reply = {"role": "assistant", "content": text, "model": "local",
                 "provider": "aptimizer", "at": now_iso(), "tier": tier,
                 "unverified_citations": [], "unconfirmed_clauses": [],
                 "verified_citations": [], "unconfirmed_values": [],
                 "code_sources": []}
        thread = (messages + [reply])[-CHAT_TURNS_STORED:]
        await db.projects.update_one({"_id": oid(project_id)},
                                     {"$set": {"apt_thread": thread}})
        logger.info("apt.context project=%s tier=none chars=0 prep_ms=%s",
                    project_id, timer.ms)
        return {"reply": reply, "thread": thread, "context_chars": 0,
                "tier": tier, "prep_ms": timer.ms}

    # Past the gate, so a model is going to be asked: query rewriting resolves follow-up
    # pronouns to find the clause text that stops it answering from memory.
    search_query = await _rewrite_query(question, messages)
    extracts = await run_in_threadpool(codesearchlib.search, search_query, APT_HITS, None)
    timer.mark("retrieval")

    # Engineering is the expensive half, so the light tier never computes it. Both halves
    # are cached on the project's updated_at, so a run of questions about an unchanged
    # project pays for the analysis once.
    full = tier == speedlib.FULL
    an, eng = speedlib.cached_analysis(proj, engine.analyse,
                                       englib.analyse_engineering,
                                       need_engineering=full)
    timer.mark("analysis")
    context = speedlib.cached_context(
        proj, tier,
        (lambda: aptlib.build(proj, an, eng)) if full
        else (lambda: speedlib.light_context(proj, an)))
    context = _attach_extracts(context, extracts)

    # What-if deterministic evaluation: if the question hypothetically modifies a design
    # parameter, re-run engineering on a copy and inject the exact deltas into context.
    if full and eng:
        whatif = _try_whatif(question, proj, an, eng)
        if whatif:
            context = {**context, "what_if_analysis": whatif}
    timer.mark("context")

    serialised = ailib.context_block(context)
    logger.info("apt.context project=%s tier=%s chars=%d approx_tokens=%d prep_ms=%s %s",
                project_id, tier, len(serialised), len(serialised) // 4,
                timer.ms, timer.marks)

    recent = messages[-CHAT_TURNS_SENT:]
    transcript = "\n\n".join(f'{m["role"].upper()}: {m["content"]}' for m in recent)
    try:
        result = await ailib.generate_markdown(
            ailib.PROMPTS["chat"],
            "Project state:\n\n" + serialised + "\n\nConversation:\n\n" + transcript,
            session_hint=f"chat-{project_id}",
            prefer_fast=not full,          # light questions do not need the strong model
        )
    except ailib.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ailib.AIFailed as exc:
        raise HTTPException(status_code=502, detail=f"Assistant failed: {exc}")

    # Citation guard. The model is given the registry to cite from; this checks what came
    # back against it, including content entailment for numerical values against extracts.
    cites = citelib.verify_with_extracts(result["text"], extracts)
    reply = {"role": "assistant", "content": result["text"],
             "model": result["model"], "provider": result["provider"],
             "at": now_iso(), "tier": tier,
             "unverified_citations": cites["unverified"],
             "unconfirmed_clauses": cites["code_only"],
             "verified_citations": cites["resolved"],
             "unconfirmed_values": cites.get("unconfirmed_values", []),
             # The passages the answer was built from, stored with the message so the
             # panel can open a citation onto the text it came from rather than asking the
             # reader to take the clause number on trust.
             "code_sources": [_source(h) for h in extracts]}

    # Storing the thread must NOT bump updated_at: that field is the analysis cache key,
    # so touching it here would invalidate the cache on every single message and undo the
    # saving this route was just rewritten to get. A conversation is not a design change.
    thread = (messages + [reply])[-CHAT_TURNS_STORED:]
    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$set": {"apt_thread": thread}})
    return {"reply": reply, "thread": thread, "context_chars": len(serialised),
            "tier": tier, "prep_ms": timer.ms}


@api.post("/projects/{project_id}/ai/chat/stream")
async def ai_chat_stream(project_id: str, body: ChatIn,
                         user: dict = Depends(get_current_user)):
    """The same answer as /ai/chat, streamed as server-sent events.

    Event shape, one JSON object per `data:` line:
        {"delta": "..."}                    a fragment of the answer
        {"done": {...reply...}, "thread":}  the finished message, stored
        {"error": "..."}                    something went wrong mid-stream

    The citation guard runs on the COMPLETED text, before the message is stored and before
    the done event goes out. Tokens reach the reader unverified -- that is what streaming
    means -- but the stored message and the flags the UI renders are checked, so nothing
    the reader can act on has skipped the guard.
    """
    proj = await load_project(project_id, user, write=True)
    messages = [m for m in body.messages
                if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()]
    if not messages or messages[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="Send at least one user message")

    question = str(messages[-1]["content"])
    tier = speedlib.classify(question)
    timer = speedlib.Timer()

    async def events():
        # Chit-chat never reaches a provider, so it streams as a single event.
        if tier == speedlib.NONE:
            text = speedlib.reply_for(question)
            reply = {"role": "assistant", "content": text, "model": "local",
                     "provider": "aptimizer", "at": now_iso(), "tier": tier,
                     "unverified_citations": [], "unconfirmed_clauses": [],
                     "verified_citations": [], "unconfirmed_values": [],
                     "code_sources": []}
            thread = (messages + [reply])[-CHAT_TURNS_STORED:]
            await db.projects.update_one({"_id": oid(project_id)},
                                         {"$set": {"apt_thread": thread}})
            yield "data: " + json.dumps({"delta": text}) + "\n\n"
            yield "data: " + json.dumps({"done": reply, "thread": thread}) + "\n\n"
            return

        # Same as /ai/chat: retrieval only on the paths that reach a model, with query
        # rewriting to resolve follow-up context.
        search_query = await _rewrite_query(question, messages)
        extracts = await run_in_threadpool(codesearchlib.search, search_query, APT_HITS, None)

        full = tier == speedlib.FULL
        an, eng = speedlib.cached_analysis(proj, engine.analyse,
                                           englib.analyse_engineering,
                                           need_engineering=full)
        context = speedlib.cached_context(
            proj, tier,
            (lambda: aptlib.build(proj, an, eng)) if full
            else (lambda: speedlib.light_context(proj, an)))
        context = _attach_extracts(context, extracts)

        # What-if deterministic evaluation
        if full and eng:
            whatif = _try_whatif(question, proj, an, eng)
            if whatif:
                context = {**context, "what_if_analysis": whatif}

        serialised = ailib.context_block(context)
        logger.info("apt.stream project=%s tier=%s chars=%d prep_ms=%s",
                    project_id, tier, len(serialised), timer.ms)

        recent = messages[-CHAT_TURNS_SENT:]
        transcript = "\n\n".join(f'{m["role"].upper()}: {m["content"]}' for m in recent)
        parts, model, prov = [], "", ""
        try:
            async for chunk in ailib.stream_markdown(
                    ailib.PROMPTS["chat"],
                    "Project state:\n\n" + serialised + "\n\nConversation:\n\n" + transcript,
                    session_hint=f"chat-{project_id}", prefer_fast=not full):
                if isinstance(chunk, tuple):
                    _, model, prov = chunk
                    break
                parts.append(chunk)
                yield "data: " + json.dumps({"delta": chunk}) + "\n\n"
        except Exception as exc:
            logger.warning("apt.stream failed, falling back: %s", exc)
            # A stream that dies before producing anything can still be answered the
            # ordinary way; one that died mid-answer cannot be restarted without
            # rewriting text the reader has already seen.
            if parts:
                yield "data: " + json.dumps({"error": str(exc)}) + "\n\n"
                return
            try:
                result = await ailib.generate_markdown(
                    ailib.PROMPTS["chat"],
                    "Project state:\n\n" + serialised + "\n\nConversation:\n\n" + transcript,
                    session_hint=f"chat-{project_id}", prefer_fast=not full)
                parts, model, prov = [result["text"]], result["model"], result["provider"]
                yield "data: " + json.dumps({"delta": result["text"]}) + "\n\n"
            except Exception as exc2:
                yield "data: " + json.dumps({"error": str(exc2)}) + "\n\n"
                return

        text = "".join(parts)
        cites = citelib.verify_with_extracts(text, extracts)
        reply = {"role": "assistant", "content": text, "model": model or "unknown",
                 "provider": prov or "unknown", "at": now_iso(), "tier": tier,
                 "unverified_citations": cites["unverified"],
                 "unconfirmed_clauses": cites["code_only"],
                 "verified_citations": cites["resolved"],
                 "unconfirmed_values": cites.get("unconfirmed_values", []),
                 "code_sources": [_source(h) for h in extracts]}
        thread = (messages + [reply])[-CHAT_TURNS_STORED:]
        await db.projects.update_one({"_id": oid(project_id)},
                                     {"$set": {"apt_thread": thread}})
        yield "data: " + json.dumps({"done": reply, "thread": thread}) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@api.get("/projects/{project_id}/ai/chat")
async def ai_chat_thread(project_id: str, user: dict = Depends(get_current_user)):
    """The stored thread, so the panel reopens where the user left it."""
    proj = await load_project(project_id, user)
    return {"thread": proj.get("apt_thread") or []}


@api.delete("/projects/{project_id}/ai/chat")
async def ai_chat_clear(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    await db.projects.update_one({"_id": oid(project_id)},
                                 {"$unset": {"apt_thread": ""}})
    return {"thread": []}


@api.get("/projects/{project_id}/ai/chat/suggestions")
async def ai_chat_suggestions(project_id: str, module: str = "",
                              user: dict = Depends(get_current_user)):
    """Opening questions for the module the user is on, built from its own numbers.

    Cached per project version per module: the panel refetches on every module switch, and
    without the cache each switch would re-analyse the project.
    """
    proj = await load_project(project_id, user)
    key = f'{speedlib.cache_key(proj)}:suggest:{module or "general"}'

    def build():
        an, _ = speedlib.cached_analysis(proj, engine.analyse,
                                         englib.analyse_engineering,
                                         need_engineering=(module == "engineering"))
        ctx = {}
        if module == "engineering":
            _, eng = speedlib.cached_analysis(proj, engine.analyse,
                                              englib.analyse_engineering)
            ctx["engineering"] = eng
        if module in ("programme", ""):
            try:
                plan = schedlib.plan_schedule(proj, an, (proj.get("schedule") or {}),
                                              summary=True)
                if plan.get("ok"):
                    ctx["programme"] = plan
            except Exception:
                pass
        if module == "finance":
            try:
                ctx["finance"] = financelib.analyse(proj, an, proj.get("finance"))
            except Exception:
                pass
        return {"suggestions": suggestlib.build(proj, an, module, ctx), "module": module}

    return speedlib.cached_context(proj, key, build)


# ---------------------------------------------------------------- optimisers
class OptimiseIn(BaseModel):
    """`target_budget` drives the budget search; 0 means "ten percent under today"."""
    target_budget: float = 0.0


@api.post("/projects/{project_id}/optimise")
async def project_optimise(project_id: str, body: OptimiseIn = OptimiseIn(),
                           user: dict = Depends(get_current_user)):
    """Waste, grade, budget and material optimisers over the current scheme."""
    proj = await load_project(project_id, user)
    an = engine.analyse(proj)
    eng = englib.analyse_engineering(proj, an)
    return optlib.analyse(proj, an, eng, body.target_budget)


@api.post("/projects/{project_id}/ai/optimise")
async def ai_optimise(project_id: str, body: OptimiseIn = OptimiseIn(),
                      user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    an = engine.analyse(proj)
    eng = englib.analyse_engineering(proj, an)
    opt = optlib.analyse(proj, an, eng, body.target_budget)
    context = {
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "current_cost_inr": an["cost"]["total"],
        # Only the decision-shaped parts: what it is now, what it could be, and the levers.
        # The full option grids run to hundreds of rows and say nothing a reader needs.
        "optimisers": {
            k: {"current": v["current"], "best": v["best"], "delta": v["delta"],
                "changes": v["changes"], "feasible": v["feasible"], "notes": v["notes"]}
            for k, v in opt.items() if isinstance(v, dict) and "current" in v
        },
    }
    return await _run_ai("optimise", context, store_at="ai.optimise",
                         project_id=project_id, user=user, activity="ai.optimise")


@api.post("/projects/{project_id}/optimise/planning")
async def project_plan_optimise(project_id: str, user: dict = Depends(get_current_user)):
    """Floor, FAR, FSI, open space, mix, parking and utility optimisers."""
    proj = await load_project(project_id, user)
    return planoptlib.analyse(proj, engine.analyse(proj))


@api.post("/projects/{project_id}/ai/planning")
async def ai_planning(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    an = engine.analyse(proj)
    opt = planoptlib.analyse(proj, an)
    context = {
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "scheme": {"far": an["areas"]["far"], "units": an["areas"]["total_units"],
                   "open_space_pct": an["areas"]["open_space_pct"],
                   "max_height_m": an["areas"]["max_height_m"]},
        # Decision-shaped parts only. The option grids run to dozens of rows each and say
        # nothing the reader needs.
        "optimisers": {
            k: {"current": v["current"], "best": v["best"], "delta": v["delta"],
                "changes": v["changes"], "feasible": v["feasible"], "notes": v["notes"]}
            for k, v in opt.items() if isinstance(v, dict) and "current" in v
        },
    }
    return await _run_ai("planning", context, store_at="ai.planning",
                         project_id=project_id, user=user, activity="ai.planning")


# ------------------------------------------------------------ AI Engineering Consultant
# A topic-driven advisory memo. Distinct from the chat copilot: one question, one
# structured memo, grounded only in figures the engine has already computed.

class ConsultIn(BaseModel):
    topic: str = "general"
    question: str = ""


_CONSULT_TOPICS = {
    "seismic": {"label": "Seismic design", "slice": "engineering"},
    "wind": {"label": "Wind loading", "slice": "gis"},
    "flood": {"label": "Flood & drainage", "slice": "gis"},
    "layout": {"label": "Layout & massing", "slice": "planning"},
    "cost": {"label": "Cost & quantities", "slice": "cost"},
    "compliance": {"label": "Compliance & approvals", "slice": "compliance"},
    "parking": {"label": "Parking & access", "slice": "planning"},
    "general": {"label": "General advisory", "slice": "summary"},
}


@api.get("/ai/consult/topics")
async def ai_consult_topics(user: dict = Depends(get_current_user)):
    return [{"id": k, "label": v["label"]} for k, v in _CONSULT_TOPICS.items()]


@api.post("/projects/{project_id}/ai/consult")
async def ai_consult(project_id: str, body: ConsultIn,
                     user: dict = Depends(get_current_user)):
    topic = body.topic if body.topic in _CONSULT_TOPICS else "general"
    proj = await load_project(project_id, user)
    an = engine.analyse(proj)
    areas, comp, cost, park = an["areas"], an["compliance"], an["cost"], an["parking"]
    context = {
        "topic": _CONSULT_TOPICS[topic]["label"],
        "question": body.question or None,
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "scheme": {"plot_area_sqm": areas["plot_area_sqm"], "far": areas["far"],
                   "ground_coverage_pct": areas["ground_coverage_pct"],
                   "open_space_pct": areas["open_space_pct"],
                   "total_units": areas["total_units"], "towers": len(proj.get("towers") or []),
                   "max_height_m": areas["max_height_m"]},
        "capacity_forecast": an.get("capacity_forecast"),
        "parking": {"required": park["required_slots"], "provided": park["provided_slots"]},
        "cost_inr": {"total": cost["total"], "per_unit": cost["per_unit"],
                     "per_sqm": cost.get("per_sqm")},
    }
    if topic == "seismic":
        eng = englib.analyse_engineering(proj, an)
        context["engineering"] = {k: {"outputs": m.get("outputs"),
                                       "recommendation": m.get("recommendation"),
                                       "code_refs": m.get("code_refs") or m.get("codes")}
                                   for k, m in (eng.get("modules") or {}).items()
                                   if k in ("seismic", "loads", "foundation")}
        context["warnings"] = eng.get("warnings")
    elif topic in ("wind", "flood"):
        g = proj.get("gis") or {}
        context["gis"] = {k: g.get(k) for k in ("wind", "flood", "seismic", "location")
                          if g.get(k) is not None}
        if not context["gis"]:
            raise HTTPException(status_code=400,
                                detail="Run the site analysis first -- there is no GIS data to advise on")
    elif topic in ("layout", "parking"):
        context["optimisers"] = {k: {"current": v.get("current"), "best": v.get("best"),
                                     "changes": v.get("changes"), "feasible": v.get("feasible")}
                                 for k, v in (planoptlib.analyse(proj, an) or {}).items()
                                 if isinstance(v, dict) and "current" in v}
    elif topic == "cost":
        context["quantities"] = an["quantities"]
        context["boq"] = {k: v for k, v in an["boq"].items() if k != "currency"}
    elif topic == "compliance":
        context["rules"] = [{"code": r["code"], "label": r["label"], "requirement":
                             f"{r['operator']} {r['threshold']}{r.get('unit') or ''}",
                             "actual": r["actual"], "status": r["status"]}
                            for r in comp["results"]]
        context["score_pct"] = comp["score"]
    return await _run_ai("consult", context, store_at=f"ai.consult.{topic}",
                         project_id=project_id, user=user, activity=f"ai.consult.{topic}")


# ------------------------------------------------------------- Explainable AI
# "Explain this figure": the engine re-shows one number it computed, with its own
# derivation. The registry below maps figure keys to the derivation data already
# present in the analysis -- nothing is recomputed and nothing is invented.


def _explain_context(figure: str, proj: dict, an: dict) -> dict:
    areas, cost, park = an["areas"], an["cost"], an["parking"]
    if figure == "far":
        far_rule = next((r for r in an["compliance"]["results"]
                         if r.get("param") == "far"), None)
        return {"figure": "Floor Area Ratio (FAR)", "value": areas["far"], "unit": "ratio",
                "inputs": {"plot_area_sqm": areas["plot_area_sqm"],
                           "total_floor_area_sqm": areas["builtup_area_sqm"]},
                "formula": "FAR = total covered floor area / plot area",
                "steps": [f"total floor area = {areas['builtup_area_sqm']} sqm",
                          f"plot area = {areas['plot_area_sqm']} sqm",
                          f"FAR = {areas['builtup_area_sqm']} / {areas['plot_area_sqm']} = {areas['far']}"],
                "code_ref": (far_rule or {}).get("code") or "NBC / local Development Control rules",
                "limit": (far_rule or {}).get("threshold")}
    if figure == "ground_coverage":
        gc_rule = next((r for r in an["compliance"]["results"]
                        if r.get("param") in ("ground_coverage_pct", "ground_coverage")), None)
        return {"figure": "Ground coverage", "value": areas["ground_coverage_pct"], "unit": "%",
                "inputs": {"plot_area_sqm": areas["plot_area_sqm"],
                           "footprint_sqm": areas.get("ground_footprint_sqm")},
                "formula": "coverage % = tower footprint area / plot area x 100",
                "code_ref": (gc_rule or {}).get("code"),
                "limit": (gc_rule or {}).get("threshold")}
    if figure == "total_units":
        tower_rows = []
        for t in (areas.get("towers") or []):
            tower_rows.append({"name": t.get("name"), "floors": t.get("floors"),
                               "units_per_floor": t.get("units_per_floor"),
                               "tower_total": t.get("total_units")})
        return {"figure": "Total dwelling units", "value": areas["total_units"], "unit": "units",
                "inputs": {"towers": tower_rows},
                "formula": "total units = sum over towers of unit counts x occupied storeys",
                "steps": [f"{r['name']}: {r['units_per_floor']} units/floor ({r['floors']} floors) = {r['tower_total']}"
                          for r in tower_rows] + [f"total = {areas['total_units']}"],
                "code_ref": None}
    if figure == "parking_required":
        return {"figure": "Parking requirement", "value": park["required_slots"], "unit": "ECS",
                "inputs": {"total_units": areas["total_units"],
                           "provided_slots": park["provided_slots"]},
                "formula": "ECS demand derived from unit count per parking.py demand model",
                "code_ref": park.get("code_ref") or "NBC Part 3 / local DC rules",
                "deficit": park.get("deficit")}
    if figure == "cost_total":
        return {"figure": "Total estimated cost", "value": cost["total"], "unit": "INR",
                "inputs": {"builtup_area_sqm": areas["builtup_area_sqm"],
                           "per_sqm_rate": cost.get("per_sqm"),
                           "per_unit": cost["per_unit"]},
                "formula": "total = sum of BOQ head costs (takeoff.py quantities x configured rates)",
                "code_ref": None}
    if figure == "seismic_base_shear":
        eng = englib.analyse_engineering(proj, an)
        seis = ((eng.get("modules") or {}).get("seismic") or {})
        derived = seis.get("derived") or {}
        raw_outs = seis.get("outputs") or []
        outs = ({item.get("label", f"item_{idx}"): item.get("value")
                 for idx, item in enumerate(raw_outs) if isinstance(item, dict)}
                if isinstance(raw_outs, list) else dict(raw_outs))
        val = derived.get("vb_kn") if derived.get("vb_kn") is not None else (
            outs.get("base_shear_kn") or outs.get("base_shear") or outs.get("Design base shear VB")
        )
        return {"figure": "Seismic base shear", "value": val, "unit": "kN",
                "inputs": outs, "derived": derived,
                "formula": "V = Ah x W  (IS 1893:2016 Cl. 7.6.2)",
                "code_ref": "IS 1893:2016 Cl. 7.6.2",
                "recommendation": seis.get("recommendation")}
    raise HTTPException(status_code=400, detail={
        "message": f"Unknown figure '{figure}'",
        "supported": ["far", "ground_coverage", "total_units", "parking_required",
                      "cost_total", "seismic_base_shear"]})


@api.get("/projects/{project_id}/ai/explain/figures")
async def ai_explain_figures(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    an = engine.analyse(proj)
    areas, cost, park = an["areas"], an["cost"], an["parking"]
    return [{"id": "far", "label": "FAR", "value": areas["far"]},
            {"id": "ground_coverage", "label": "Ground coverage %",
             "value": areas["ground_coverage_pct"]},
            {"id": "total_units", "label": "Total units", "value": areas["total_units"]},
            {"id": "parking_required", "label": "Parking required (ECS)",
             "value": park["required_slots"]},
            {"id": "cost_total", "label": "Total cost",
             "value": cost["total"]},
            {"id": "seismic_base_shear", "label": "Seismic base shear (kN)",
             "value": None}]  # value filled by the engineering module


@api.post("/projects/{project_id}/ai/explain/{figure}")
async def ai_explain(project_id: str, figure: str,
                     user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    an = engine.analyse(proj)
    context = _explain_context(figure, proj, an)
    return await _run_ai("explain", context, project_id=project_id, user=user,
                         activity=f"ai.explain.{figure}")


# ------------------------------------------------- Smart Building Recommendations
@api.get("/projects/{project_id}/recommendations")
async def project_recommendations(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return reclib.recommendations(proj, engine.analyse(proj))


# ---------------------------------------------------------------- development finance
class FinanceIn(BaseModel):
    """Partial config; anything omitted falls back to FinanceConfig defaults."""
    config: Dict[str, Any] = Field(default_factory=dict)
    save: bool = True           # keep the inputs on the project so the tab reopens as left


@api.get("/finance/defaults")
async def finance_defaults(user: dict = Depends(get_current_user)):
    return financelib.FinanceConfig().to_dict()


@api.post("/projects/{project_id}/finance")
async def project_finance(project_id: str, body: FinanceIn,
                          user: dict = Depends(get_current_user)):
    """Revenue, profit, return and cash flow for a project the engine can already price."""
    proj = await load_project(project_id, user, write=body.save)
    merged_cfg = {**(proj.get("finance") or {}), **(body.config or {})}
    result = financelib.analyse(proj, engine.analyse(proj), merged_cfg)
    if body.save:
        await db.projects.update_one(
            {"_id": oid(project_id)},
            {"$inc": {"rev": 1}, "$set": {"finance": result["config"], "updated_at": now_iso()}})
    return result


@api.post("/projects/{project_id}/ai/finance")
async def ai_finance(project_id: str, body: FinanceIn = FinanceIn(),
                     user: dict = Depends(get_current_user)):
    """Body is optional: with none, the assumptions last saved on the project are used."""
    proj = await load_project(project_id, user, write=True)
    an = engine.analyse(proj)
    merged_cfg = {**(proj.get("finance") or {}), **(body.config or {})}
    fin = financelib.analyse(proj, an, merged_cfg)
    context = {
        "project": {"name": proj.get("name"), "location": proj.get("location")},
        "scale": {"builtup_area_sqm": an["areas"]["builtup_area_sqm"],
                  "total_units": an["areas"]["total_units"],
                  "saleable_sqft": fin["saleable"]["total_sqft"]},
        "assumptions": fin["config"],
        "revenue_inr": fin["revenue"],
        "cost_inr": fin["cost"],
        "profit_inr": fin["profit"],
        "break_even": fin["break_even"],
        "timing": fin["timing"],
    }
    return await _run_ai("finance", context, store_at="ai.finance",
                         project_id=project_id, user=user, activity="ai.finance")


# ---------------------------------------------------------------- programme
class ScheduleIn(BaseModel):
    """Partial config; anything omitted falls back to ScheduleConfig defaults."""
    config: Dict[str, Any] = Field(default_factory=dict)
    summary: bool = False       # headline figures only -- see schedule.plan_schedule


@api.get("/schedule/defaults")
async def schedule_defaults(user: dict = Depends(get_current_user)):
    return {"config": schedlib.ScheduleConfig().to_dict(),
            "formwork_is456": schedlib.FORMWORK_IS456,
            "curing_min_days": schedlib.CURING_MIN_DAYS}


@api.post("/projects/{project_id}/schedule")
async def build_schedule(project_id: str, body: ScheduleIn,
                         user: dict = Depends(get_current_user)):
    """Derive the construction programme from the project's own quantities."""
    proj = await load_project(project_id, user)
    merged_cfg = {**(proj.get("schedule") or {}), **(body.config or {})}
    return schedlib.plan_schedule(proj, engine.analyse(proj), merged_cfg, summary=body.summary)


class ScheduleLiveIn(ScheduleIn):
    project: Dict[str, Any]


@api.post("/schedule")
async def build_schedule_live(body: ScheduleLiveIn, user: dict = Depends(get_current_user)):
    """Stateless variant for live editing before save."""
    merged_cfg = {**((body.project or {}).get("schedule") or {}), **(body.config or {})}
    return schedlib.plan_schedule(body.project, engine.analyse(body.project), merged_cfg,
                                  summary=body.summary)


# ---------------------------------------------------------------- versions
@api.get("/projects/{project_id}/versions")
async def list_versions(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.versions.find({"project_id": project_id}).sort("at", -1).to_list(100)
    return [{"id": str(d["_id"]), "label": d["label"], "at": d["at"],
             "user_name": d.get("user_name", "")} for d in docs]


@api.post("/projects/{project_id}/versions")
async def create_version(project_id: str, body: VersionIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    proj.pop("_access_role", None)
    snapshot = serialize_project(proj)
    snapshot.pop("id", None)
    snapshot.pop("public_token", None)
    res = await db.versions.insert_one({
        "project_id": project_id, "label": body.label, "at": now_iso(),
        "user_name": user.get("name") or user.get("email"), "snapshot": snapshot})
    await log_activity(project_id, user, "version.saved", body.label)
    return {"id": str(res.inserted_id), "label": body.label}


@api.post("/projects/{project_id}/versions/{version_id}/restore")
async def restore_version(project_id: str, version_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    v = await db.versions.find_one({"_id": oid(version_id), "project_id": project_id})
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    snap = dict(v["snapshot"])
    snap.pop("created_at", None)
    # A snapshot taken after revisions existed carries the revision it was saved at. Setting
    # that back while also incrementing it is two writes to one path, which Mongo rejects
    # outright — and restoring an OLD revision number would let a client holding the current
    # one overwrite the restore. The revision only ever moves forward.
    snap.pop("rev", None)
    snap["updated_at"] = now_iso()
    await db.projects.update_one({"_id": oid(project_id)}, {"$inc": {"rev": 1}, "$set": snap})
    await log_activity(project_id, user, "version.restored", v["label"])
    proj = await db.projects.find_one({"_id": oid(project_id)})
    return serialize_project(proj)


# ---------------------------------------------------------------- sharing & activity
@api.get("/projects/{project_id}/shares")
async def list_shares(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.shares.find({"project_id": project_id}).to_list(100)
    return [{"id": str(d["_id"]), "email": d["email"], "role": d["role"],
             "user_id": d["user_id"], "at": d.get("at")} for d in docs]


@api.post("/projects/{project_id}/shares")
async def share_project(project_id: str, body: ShareIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    if proj["_access_role"] != "admin":
        raise HTTPException(status_code=403, detail="Only project admins can share")
    target = await db.users.find_one({"email": body.email.lower()})
    if not target:
        raise HTTPException(status_code=404, detail="No registered user with that email")
    if str(target["_id"]) == str(proj["owner_id"]):
        raise HTTPException(status_code=400, detail="Owner already has full access")
    role = body.role if body.role in authlib.ROLES else "viewer"
    await db.shares.update_one(
        {"project_id": project_id, "user_id": str(target["_id"])},
        {"$set": {"email": target["email"], "role": role, "at": now_iso()}}, upsert=True)
    await log_activity(project_id, user, "project.shared", f"{target['email']} as {role}")
    return {"ok": True, "email": target["email"], "role": role}


@api.delete("/projects/{project_id}/shares/{share_id}")
async def unshare(project_id: str, share_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    if proj["_access_role"] != "admin":
        raise HTTPException(status_code=403, detail="Only project admins can manage sharing")
    await db.shares.delete_one({"_id": oid(share_id)})
    await log_activity(project_id, user, "project.unshared", share_id)
    return {"ok": True}


@api.get("/projects/{project_id}/activity")
async def project_activity(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.activity.find({"project_id": project_id}).sort("at", -1).to_list(200)
    return [{"id": str(d["_id"]), "user_name": d.get("user_name"), "action": d["action"],
             "detail": d.get("detail", ""), "at": d["at"]} for d in docs]


# ---------------------------------------------------------------- V4 Task Assignment
class TaskCreateIn(BaseModel):
    title: str
    description: str = ""
    assigned_to: str = ""
    role: str = "engineer"
    stage: str = "Design"
    priority: str = "medium"
    due_date: Optional[str] = None


class TaskUpdateIn(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    role: Optional[str] = None
    stage: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[str] = None


@api.get("/projects/{project_id}/tasks")
async def list_tasks(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.tasks.find({"project_id": project_id}).sort("created_at", -1).to_list(200)
    for d in docs:
        d.pop("_id", None)
    return docs


@api.post("/projects/{project_id}/tasks")
async def create_task(project_id: str, body: TaskCreateIn, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    task = collablib.TaskManager.create_task(
        title=body.title,
        description=body.description,
        assigned_to=body.assigned_to,
        role=body.role,
        stage=body.stage,
        priority=body.priority,
        due_date=body.due_date,
        created_by=user.get("name") or user.get("email", "")
    )
    task["project_id"] = project_id
    await db.tasks.insert_one(dict(task))
    await log_activity(project_id, user, "task.created", task["title"])
    task.pop("_id", None)
    return task


@api.patch("/projects/{project_id}/tasks/{task_id}")
async def update_task(project_id: str, task_id: str, body: TaskUpdateIn, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    task = await db.tasks.find_one({"id": task_id, "project_id": project_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    updated = collablib.TaskManager.update_task(task, body.dict(exclude_unset=True))
    await db.tasks.update_one({"id": task_id, "project_id": project_id}, {"$set": updated})
    await log_activity(project_id, user, "task.updated", f"{task['title']} ({updated.get('status')})")
    updated.pop("_id", None)
    return updated


@api.delete("/projects/{project_id}/tasks/{task_id}")
async def delete_task(project_id: str, task_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    await db.tasks.delete_one({"id": task_id, "project_id": project_id})
    await log_activity(project_id, user, "task.deleted", task_id)
    return {"ok": True}


# ---------------------------------------------------------------- V4 Approval Workflow
class ApprovalActionIn(BaseModel):
    action: str  # 'submit', 'approve', 'request_changes'
    notes: str = ""


@api.get("/projects/{project_id}/approvals")
async def list_approvals(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.approvals.find({"project_id": project_id}).to_list(50)
    if not docs:
        defaults = collablib.ApprovalWorkflow.initialize_stage_approvals()
        for d in defaults:
            d["project_id"] = project_id
            await db.approvals.insert_one(dict(d))
            d.pop("_id", None)
        return defaults
    for d in docs:
        d.pop("_id", None)
    return docs


@api.get("/projects/{project_id}/approvals/{approval_id}/verify")
async def verify_approval_certificate(project_id: str, approval_id: str, user: dict = Depends(get_current_user)):
    """Check an approval stamp's HMAC signature: true only if unaltered since sign-off."""
    await load_project(project_id, user)
    approval = await db.approvals.find_one({"id": approval_id, "project_id": project_id}, {"_id": 0})
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    stamp = approval.get("stamp") or {}
    return {"approval_id": approval_id, "certificate_id": stamp.get("certificate_id"),
            "signed": bool(stamp.get("signature")), "valid": collablib.verify_stamp(stamp)}


@api.post("/projects/{project_id}/approvals/{approval_id}/action")
async def approval_action(project_id: str, approval_id: str, body: ApprovalActionIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    approval = await db.approvals.find_one({"id": approval_id, "project_id": project_id})
    if not approval:
        raise HTTPException(status_code=404, detail="Approval gate not found")
    
    user_name = user.get("name") or user.get("email", "Reviewer")
    user_role = proj.get("_access_role") or user.get("role", "engineer")
    
    if body.action == "submit":
        updated = collablib.ApprovalWorkflow.submit_for_review(approval, user_name, body.notes)
    elif body.action in ("approve", "request_changes"):
        updated = collablib.ApprovalWorkflow.review_action(approval, user_name, user_role, body.action, body.notes)
        if body.action == "approve":
            snapshot_label = f"Certified Approval - {approval['stage']}"
            await db.versions.insert_one({
                "project_id": project_id, "label": snapshot_label, "at": collablib.now_iso(),
                "user_name": user_name, "snapshot": serialize_project(proj)
            })
    else:
        raise HTTPException(status_code=400, detail="Invalid approval action")
        
    await db.approvals.update_one({"id": approval_id, "project_id": project_id}, {"$set": updated})
    await log_activity(project_id, user, f"approval.{body.action}", f"{approval['stage']} - {body.action}")
    updated.pop("_id", None)

    # The automation engine listens to the same events it is configured for. A sign-off
    # fires stage_approved here so the snapshot rule runs through the one execution path
    # instead of this endpoint hand-rolling a second one.
    if body.action == "approve":
        try:
            await run_automation_trigger(
                project_id,
                AutomationTriggerIn(trigger="stage_approved",
                                    context={"stage": approval["stage"], "approved_by": user_name}),
                user)
        except Exception:
            logger.exception("automation hook failed after approval")
    return updated


# ---------------------------------------------------------------- V4 Comments & Reviews
class CommentCreateIn(BaseModel):
    content: str
    module: str = "General"
    stage: str = "Design"
    target_ref: str = ""


class CommentReplyIn(BaseModel):
    content: str


@api.get("/projects/{project_id}/comments")
async def list_comments(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.comments.find({"project_id": project_id}).sort("created_at", -1).to_list(300)
    for d in docs:
        d.pop("_id", None)
    return docs


@api.post("/projects/{project_id}/comments")
async def create_comment(project_id: str, body: CommentCreateIn, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    author_name = user.get("name") or user.get("email", "Reviewer")
    author_email = user.get("email", "")
    comment = collablib.CommentManager.create_comment(
        author_name=author_name,
        author_email=author_email,
        content=body.content,
        module=body.module,
        stage=body.stage,
        target_ref=body.target_ref
    )
    comment["project_id"] = project_id
    await db.comments.insert_one(dict(comment))
    await log_activity(project_id, user, "comment.added", f"[{body.module}] {body.content[:40]}")
    comment.pop("_id", None)
    return comment


@api.post("/projects/{project_id}/comments/{comment_id}/reply")
async def add_comment_reply(project_id: str, comment_id: str, body: CommentReplyIn, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    comment = await db.comments.find_one({"id": comment_id, "project_id": project_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    author_name = user.get("name") or user.get("email", "Reviewer")
    updated = collablib.CommentManager.add_reply(comment, author_name, body.content)
    await db.comments.update_one({"id": comment_id, "project_id": project_id}, {"$set": {"replies": updated["replies"]}})
    updated.pop("_id", None)
    return updated


@api.patch("/projects/{project_id}/comments/{comment_id}/resolve")
async def resolve_comment(project_id: str, comment_id: str, resolved: bool = True, user: dict = Depends(get_current_user)):
    await load_project(project_id, user, write=True)
    comment = await db.comments.find_one({"id": comment_id, "project_id": project_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    user_name = user.get("name") or user.get("email", "Reviewer")
    updated = collablib.CommentManager.set_resolved(comment, resolved, user_name)
    await db.comments.update_one({"id": comment_id, "project_id": project_id}, {"$set": updated})
    updated.pop("_id", None)
    return updated


# ---------------------------------------------------------------- V4 Workflow Automation
class AutomationTriggerIn(BaseModel):
    trigger: str
    context: Dict[str, Any] = {}


@api.get("/projects/{project_id}/automations")
async def get_automations(project_id: str, user: dict = Depends(get_current_user)):
    await load_project(project_id, user)
    docs = await db.automations.find({"project_id": project_id}).to_list(50)
    if not docs:
        rules = collablib.AutomationEngine.DEFAULT_RULES
        return rules
    for d in docs:
        d.pop("_id", None)
    return docs


@api.post("/projects/{project_id}/automations/trigger")
async def run_automation_trigger(project_id: str, body: AutomationTriggerIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=True)
    actions = collablib.AutomationEngine.evaluate_triggers(body.trigger, body.context)
    results = []
    for act in actions:
        if act["action"] == "create_task":
            t = collablib.TaskManager.create_task(
                title=act["params"].get("title", "Automated Task"),
                description=f"Generated automatically by rule: {act['rule_name']}",
                role=act["params"].get("role", "engineer"),
                priority=act["params"].get("priority", "high"),
                created_by="Workflow Engine"
            )
            t["project_id"] = project_id
            await db.tasks.insert_one(dict(t))
            results.append({"rule": act["rule_name"], "result": f"Created task: {t['title']}"})
        elif act["action"] == "create_snapshot":
            label = f"{act['params'].get('label_prefix', 'Automation')} - {body.trigger}"
            await db.versions.insert_one({
                "project_id": project_id, "label": label, "at": collablib.now_iso(),
                "user_name": user.get("name") or "Workflow Engine", "snapshot": serialize_project(proj)
            })
            results.append({"rule": act["rule_name"], "result": f"Captured snapshot: {label}"})
        elif act["action"] == "invalidate_approvals":
            gates = await db.approvals.find({"project_id": project_id}).to_list(50)
            if gates:
                invalidated = collablib.AutomationEngine.invalidate_approvals(
                    gates, act["params"].get("stages") or collablib.DOWNSTREAM_OF_LAYOUT)
                for gate in invalidated:
                    await db.approvals.update_one({"id": gate["id"], "project_id": project_id}, {"$set": gate})
            reset = [g["stage"] for g in (gates or []) if g.get("status") == "draft" and g.get("history")]
            results.append({"rule": act["rule_name"],
                            "result": act["params"].get("message", "Approvals invalidated")
                                      + (f" (reset: {', '.join(reset)})" if reset else "")})
        elif act["action"] == "log_event":
            await log_activity(project_id, user, "automation.fired", act["rule_name"])
            results.append({"rule": act["rule_name"], "result": act["params"].get("message", "Logged")})
    await log_activity(project_id, user, "automation.triggered", body.trigger)
    return {"triggered": body.trigger, "triggers_evaluated": len(actions), "actions_executed": results}


@api.post("/projects/{project_id}/automation-events")
async def automation_event(project_id: str, body: AutomationTriggerIn, user: dict = Depends(get_current_user)):
    """Fire the engine for a state change that just happened, then report what it did.

    Same execution path as the manual trigger endpoint — rules, actions, persistence —
    just named for the client-side hooks that call it after a layout save or an approval
    sign-off.
    """
    return await run_automation_trigger(project_id, body, user)


@api.post("/projects/{project_id}/automation-events/compliance-check")
async def automation_compliance_check(project_id: str, user: dict = Depends(get_current_user)):
    """Recompute compliance and fire the engine with the real verdict.

    The trigger only carries a failure — a passing project fires nothing, so the rules
    exist to catch violations, not to generate noise on every check.
    """
    proj = await load_project(project_id, user)
    an = engine.analyse(proj)
    comp = an.get("compliance") or {}
    failed = [r for r in (comp.get("results") or []) if r.get("status") == "fail"]
    if not failed:
        return {"triggered": "compliance_violation", "triggers_evaluated": 0, "actions_executed": [],
                "compliance": {"failed": 0, "score": comp.get("score"), "overall": comp.get("overall")}}
    body = AutomationTriggerIn(
        trigger="compliance_violation",
        context={"failed_rules": [r.get("label") for r in failed],
                 "failed_count": len(failed), "score": comp.get("score")})
    result = await run_automation_trigger(project_id, body, user)
    result["compliance"] = {"failed": len(failed), "score": comp.get("score"),
                            "overall": comp.get("overall"),
                            "rules": [r.get("label") for r in failed[:5]]}
    return result


# ---------------------------------------------------------------- V4 Equipment Planning
@api.get("/projects/{project_id}/equipment-plan")
async def get_equipment_plan(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    base = engine.analyse(proj)
    boq = base.get("boq") or {}
    prog = None
    try:
        prog = schedlib.plan_schedule(proj, base, proj.get("schedule"), summary=True)
    except Exception:
        pass
    return equipengine.plan_equipment(proj, boq, prog)


# ---------------------------------------------------------------- V4 Utility Network Planning
@api.get("/projects/{project_id}/utility-network-plan")
async def get_utility_network_plan(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    base = engine.analyse(proj)
    return utilnetengine.plan_utility_network(proj, base)


# ---------------------------------------------------------------- X+ Generative Design
class LandscapeIn(BaseModel):
    open_space_sqm: Optional[float] = None


class ParkingGenIn(BaseModel):
    footprint_sqm: Optional[float] = None
    layout_type: str = "orthogonal"


@api.get("/projects/{project_id}/generative-design/facades")
async def get_generative_facades(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return gendesign.generate_facade_options(proj)


@api.get("/projects/{project_id}/generative-design/facade-options")
async def get_generative_facade_options(project_id: str, user: dict = Depends(get_current_user)):
    """Alias kept for the client contract: same payload, UI-friendly field names."""
    proj = await load_project(project_id, user)
    return gendesign.facade_options_ui(gendesign.generate_facade_options(proj))


@api.post("/projects/{project_id}/generative-design/landscape")
async def get_generative_landscape(project_id: str, body: LandscapeIn = LandscapeIn(), user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    base = engine.analyse(proj)
    open_space = body.open_space_sqm or base.get("areas", {}).get("open_space_sqm") or 3000.0
    return gendesign.generate_landscape_zones(open_space)


@api.post("/projects/{project_id}/generative-design/parking")
async def get_generative_parking(project_id: str, body: ParkingGenIn = ParkingGenIn(), user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    base = engine.analyse(proj)
    footprint = body.footprint_sqm or base.get("areas", {}).get("ground_footprint_sqm") or 3500.0
    return gendesign.generate_parking_layout(footprint, body.layout_type)


# ---------------------------------------------------------------- reports
@api.get("/projects/{project_id}/reports/{report_type}")
async def download_report(project_id: str, report_type: str,
                          township_area_sqm: Optional[float] = None,
                          is_mixed_use: bool = True,
                          user: dict = Depends(get_current_user)):
    """Stage 1 (Site) reports: plot, layout, site (GIS), township, or `site-stage` for all
    of them in one PDF. Other stages' reports are not part of this build."""
    resolved = reportlib.MERGED_INTO.get(report_type, report_type)
    if report_type != "site-stage" and resolved not in reportlib.SITE_REPORTS:
        raise HTTPException(status_code=400, detail=f"Unknown report type '{report_type}'")
    proj = await load_project(project_id, user)
    proj.pop("_access_role", None)

    township_plan = None
    if resolved in ("township", "site-stage") or report_type == "site-stage":
        params = {"is_mixed_use": is_mixed_use}
        if township_area_sqm:
            params["township_area_sqm"] = township_area_sqm
        township_plan = await run_in_threadpool(autoplanning.township_mixed_use_plan, proj, params)
        township_plan.setdefault("is_mixed_use", is_mixed_use)

    if resolved == "township":
        pdf = await run_in_threadpool(reportlib.build_township_pdf, proj, township_plan)
    else:
        base = await run_in_threadpool(engine.analyse, proj)
        eng = await run_in_threadpool(englib.analyse_engineering, proj, base)
        if report_type == "site-stage":
            pdf = await run_in_threadpool(reportlib.build_site_stage_pdf, proj, base, eng, township_plan)
        else:
            pdf = await run_in_threadpool(reportlib.build_pdf, resolved, proj, base, eng)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (proj.get("name") or "project"))[:40]
    name = f"{safe}_{report_type}.pdf"
    import io
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api.get("/projects/{project_id}/towers/{tower_id}/floorplan-image")
async def get_tower_floorplan_image(
    project_id: str,
    tower_id: str,
    floor: Optional[int] = None,
    dpi: int = 150,
    user: dict = Depends(get_current_user),
):
    """Render and return high-resolution 2D architectural PNG image of a tower's floor plan."""
    proj = await load_project(project_id, user)
    towers = proj.get("towers") or []
    tower = next((t for t in towers if t.get("id") == tower_id), None)
    if not tower:
        raise HTTPException(status_code=404, detail="Tower not found")

    import floorplan_render
    png_bytes = floorplan_render.render_floorplan_image(
        tower, floor=floor, project_name=proj.get("name", ""), dpi=min(max(dpi, 72), 300), show_title_block=True
    )
    tname = tower.get("name", "tower").replace(" ", "_")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{tname}_floorplan.png"'}
    )


@api.get("/projects/{project_id}/floorplans/zip")
async def download_all_floorplan_images_zip(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """Download a ZIP archive containing 2D architectural drawings (PNG) for all towers in the project."""
    import io
    import zipfile
    import floorplan_render

    proj = await load_project(project_id, user)
    towers = proj.get("towers") or []
    if not towers:
        raise HTTPException(status_code=400, detail="No towers defined in project")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, t in enumerate(towers):
            tname = t.get("name") or f"Tower_{idx + 1}"
            safe_name = re.sub(r'[^\w\-]', '_', tname)
            png_bytes = floorplan_render.render_floorplan_image(
                t, project_name=proj.get("name", ""), dpi=200, show_title_block=True
            )
            zf.writestr(f"{safe_name}_floorplan.png", png_bytes)

    zip_buf.seek(0)
    zip_name = f"{proj.get('name', 'project').replace(' ', '_')}_floorplans.zip"
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'}
    )


AI_RENDER_VIEWS = ("floorplan_3d", "exterior_3d", "interior_living")
# Billed per image on the server's own key, so the client may only pick from these.
AI_RENDER_MODELS = ("nano-banana-pro-preview", "gemini-3-pro-image", "gemini-2.5-flash-image",
                    "gemini-3.1-flash-image-preview")


class TowerAIRenderIn(BaseModel):
    view_type: str = "floorplan_3d"
    custom_prompt: Optional[str] = Field(default=None, max_length=2000)
    model: Optional[str] = "nano-banana-pro-preview"


def _render_view(view_type: str) -> str:
    # The view name becomes part of a cache file name; only known values get that far.
    if view_type not in AI_RENDER_VIEWS:
        raise HTTPException(status_code=400, detail=f"view_type must be one of {', '.join(AI_RENDER_VIEWS)}")
    return view_type


@api.post("/projects/{project_id}/towers/{tower_id}/ai-render")
async def generate_tower_ai_render(
    project_id: str,
    tower_id: str,
    req: TowerAIRenderIn,
    user: dict = Depends(get_current_user),
):
    """Generate or retrieve a 3D architectural render using Google Nano Banana or Gemini image models."""
    proj = await load_project(project_id, user, write=True)
    view_type = _render_view(req.view_type)
    model = (req.model or "nano-banana-pro-preview").removeprefix("models/")
    if model not in AI_RENDER_MODELS:
        raise HTTPException(status_code=400, detail=f"model must be one of {', '.join(AI_RENDER_MODELS)}")
    towers = proj.get("towers") or []
    tower = next((t for t in towers if t.get("id") == tower_id), None)
    if not tower:
        raise HTTPException(status_code=404, detail="Tower not found")

    import ai_render
    res = await ai_render.generate_ai_render(
        tower=tower,
        project_id=project_id,
        project_name=proj.get("name", ""),
        view_type=view_type,
        custom_prompt=req.custom_prompt,
        model=model,
    )
    return res


@api.get("/projects/{project_id}/towers/{tower_id}/ai-render")
async def get_tower_ai_render_info(
    project_id: str,
    tower_id: str,
    view_type: str = "floorplan_3d",
    user: dict = Depends(get_current_user),
):
    """Get metadata and generated status for an AI render."""
    proj = await load_project(project_id, user)
    view_type = _render_view(view_type)
    towers = proj.get("towers") or []
    tower = next((t for t in towers if t.get("id") == tower_id), None)
    if not tower:
        raise HTTPException(status_code=404, detail="Tower not found")

    import ai_render
    meta = ai_render.get_cached_render_meta(project_id, tower_id, view_type=view_type)
    has_image = ai_render.get_cached_render_bytes(project_id, tower_id, view_type=view_type) is not None
    prompt = ai_render.build_architectural_prompt(tower, proj.get("name", ""), view_type=view_type)
    return {
        "has_image": has_image,
        "meta": meta,
        "prompt": prompt,
        "image_url": f"/api/projects/{project_id}/towers/{tower_id}/ai-render-image?view_type={view_type}" if has_image else None,
    }


@api.get("/projects/{project_id}/towers/{tower_id}/ai-render-image")
async def get_tower_ai_render_image(
    project_id: str,
    tower_id: str,
    view_type: str = "floorplan_3d",
    user: dict = Depends(get_current_user),
):
    """Serve the raw PNG image of the generated 3D AI render."""
    await load_project(project_id, user)   # same access rule as every other project read
    view_type = _render_view(view_type)
    import ai_render
    png_bytes = ai_render.get_cached_render_bytes(project_id, tower_id, view_type=view_type)
    if not png_bytes:
        raise HTTPException(status_code=404, detail="AI render image not found")

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{tower_id}_{view_type}.png"'}
    )


@api.get("/projects/{project_id}/boq.xlsx")
async def download_boq_excel(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    proj.pop("_access_role", None)
    xl = reportlib.build_boq_excel(proj, engine.analyse(proj))
    name = f"{proj.get('name', 'project').replace(' ', '_')}_BOQ.xlsx"
    import io
    return StreamingResponse(
        io.BytesIO(xl),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api.get("/projects/{project_id}/data-health")
async def project_data_health(project_id: str, user: dict = Depends(get_current_user)):
    """Whether the project's inputs are complete, fresh and self-consistent.

    Read-only and derived: it stores nothing and changes nothing, so it can be polled as
    the user edits without becoming another thing that can go stale.
    """
    proj = await load_project(project_id, user)
    return datahealthlib.report(proj)


@api.post("/data-health")
async def data_health_live(body: AnalyseIn, user: dict = Depends(get_current_user)):
    """Same report for an unsaved project document — mirrors /analyse."""
    return datahealthlib.report(body.project)


@api.get("/defaults")
async def get_defaults():
    return {"ratios": engine.DEFAULT_RATIOS, "rates": engine.DEFAULT_RATES,
            "rules": engine.DEFAULT_RULES,
            "unit_types": ["studio", "1bhk", "2bhk", "3bhk", "4bhk", "penthouse", "custom"],
            "room_types": ["living", "bedroom", "kitchen", "bathroom", "balcony", "utility", "common"],
            "stair_types": ["dog-legged", "open-well", "spiral", "straight-flight"]}


@api.get("/")
async def root():
    return {"service": "Aptimizer API", "status": "ok"}


# ---------------------------------------------------------------- BIM & CAD interchange
class BoundaryImportIn(BaseModel):
    coordinates: List[List[float]]
    source: str = ""
    area_sqm: Optional[float] = None


MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # a site DXF is a few MB; 20 MB is generous


async def read_upload(file: UploadFile, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an upload in chunks and stop at `limit`, so one request cannot exhaust memory."""
    chunks, size = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"File is larger than {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@api.post("/bim/dxf/inspect")
async def bim_dxf_inspect(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Inventory an uploaded DXF: layers, entity counts, closed rings, areas."""
    if not (file.filename or "").lower().endswith((".dxf", ".dwg")):
        raise HTTPException(status_code=400, detail="Upload a .dxf drawing (DWG is not readable — "
                                                    "re-save it from AutoCAD as DXF first).")
    data = await read_upload(file)
    try:
        inv = await run_in_threadpool(bimlib.inspect_dxf, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**inv, "filename": file.filename, "size_bytes": len(data)}


@api.post("/projects/{project_id}/bim/dxf/import-boundary")
async def bim_dxf_import_boundary(project_id: str, file: UploadFile = File(...),
                                  layer: str = Form(...),
                                  user: dict = Depends(get_current_user)):
    """DXF layer's largest closed ring -> the project's plot boundary (saved)."""
    proj = await load_project(project_id, user, write=True)
    data = await read_upload(file)
    # A project that already has a plot keeps its position: the imported ring is
    # centred on the existing centroid so roads, towers and the 3D view do not jump.
    existing = (proj.get("plot") or {}).get("coordinates") or []
    origin = ([sum(c[0] for c in existing) / len(existing),
               sum(c[1] for c in existing) / len(existing)] if existing else None)
    try:
        imp = await run_in_threadpool(bimlib.import_boundary, data, layer, origin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    plot = dict(proj.get("plot") or {})
    plot["coordinates"] = imp["coordinates"]
    plot["area_sqm"] = imp["area_sqm"]
    plot["imported_from"] = imp["source"]
    res = await db.projects.update_one(
        {"_id": oid(project_id)},
        {"$set": {"plot": plot, "updated_at": now_iso()}, "$inc": {"rev": 1}})
    if res.matched_count == 0:
        raise HTTPException(status_code=409, detail="Project changed while importing — retry")
    await log_activity(project_id, user, "plot.imported_dxf", f"{imp['vertex_count']} vertices")
    return {"ok": True, "area_sqm": imp["area_sqm"], "vertex_count": imp["vertex_count"],
            "source": imp["source"], "plot": plot}


@api.get("/projects/{project_id}/bim/summary")
async def bim_summary(project_id: str, user: dict = Depends(get_current_user)):
    """What the exports would contain, for the pre-download cards."""
    proj = await load_project(project_id, user)
    return {"dxf": bimlib.dxf_summary(proj), "ifc": bimlib.ifc_summary(proj)}


@api.get("/projects/{project_id}/bim/export/dxf")
async def bim_export_dxf(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    data = await run_in_threadpool(bimlib.export_siteplan_dxf, proj)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (proj.get("name") or "siteplan"))[:40]
    return Response(content=data, media_type="application/dxf",
                    headers={"Content-Disposition": f'attachment; filename="{safe}_siteplan.dxf"'})


@api.get("/projects/{project_id}/bim/export/ifc")
async def bim_export_ifc(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    data = await run_in_threadpool(bimlib.export_ifc4, proj)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (proj.get("name") or "scheme"))[:40]
    return Response(content=data, media_type="application/x-step",
                    headers={"Content-Disposition": f'attachment; filename="{safe}_scheme.ifc"'})


@api.get("/projects/{project_id}/bim/export/dwg")
async def bim_export_dwg(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    data, fmt = await run_in_threadpool(bimlib.export_siteplan_cad, proj)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (proj.get("name") or "siteplan"))[:40]
    # Without the ODA converter the server can only write DXF, so it says so in the file
    # name and a header instead of passing DXF off as DWG.
    media = "application/acad" if fmt == "dwg" else "application/dxf"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{safe}_siteplan.{fmt}"',
                             "X-Export-Format": fmt})


# --------------------------------------------------------------------------- Autonomous Engineering & Intelligent Planning
class OneClickIn(BaseModel):
    name: Optional[str] = "Aptimizer Autonomous Scheme"
    plot_area_sqm: Optional[float] = 10000.0
    target_tier: Optional[str] = "mid"
    city: Optional[str] = "Bengaluru"
    target_far: Optional[float] = 2.75
    floors: Optional[int] = 14
    save: Optional[bool] = False


@api.post("/projects/one-click-generate")
async def one_click_generate_route(body: OneClickIn, user: dict = Depends(get_current_user)):
    res = await run_in_threadpool(autoplanning.one_click_generate, body.dict())
    if body.save:
        doc = default_project(
            res["name"], res.get("client", ""), res.get("location", ""), "",
            str(user["_id"]),
        )
        doc["plot"] = res["plot"]
        doc["dev_controls"] = res["dev_controls"]
        doc["towers"] = res["towers"]
        doc["unit_mix"] = res["unit_mix"]
        doc["residential_policy"] = res["residential_policy"]
        doc["parking"] = res["parking"]
        ins = await db.projects.insert_one(doc)
        doc["_id"] = ins.inserted_id
        await log_activity(str(ins.inserted_id), user, "project.one_click_created", f"Auto-synthesized {body.target_tier} scheme")
        return {"ok": True, "project_id": str(ins.inserted_id), "scheme": res}
    return {"ok": True, "scheme": res}


class ConversationalIn(BaseModel):
    instruction: str
    save: Optional[bool] = False


@api.post("/projects/{project_id}/conversational-design")
async def conversational_design_route(project_id: str, body: ConversationalIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user, write=body.save)
    res = await run_in_threadpool(autoplanning.conversational_design, proj, body.instruction)
    if body.save and res.get("mutations_applied"):
        up = res["updated_project"]
        updates = {key: up[key] for key in (
            "towers", "unit_mix", "parking", "dev_controls", "residential_policy", "achieved_metrics"
        ) if key in up and up[key] is not None}
        updates["updated_at"] = now_iso()
        await db.projects.update_one(
            {"_id": oid(project_id)},
            {"$set": updates, "$inc": {"rev": 1}}
        )
        await log_activity(project_id, user, "project.conversational_mutation", "; ".join(res["mutations_applied"]))
    return res


@api.post("/projects/{project_id}/multi-agent-review")
async def multi_agent_review_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(autoplanning.multi_agent_review, proj)


@api.post("/projects/{project_id}/autonomous-compliance")
async def autonomous_compliance_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(autoplanning.autonomous_compliance_audit, proj)


@api.post("/projects/{project_id}/autonomous-boq")
async def autonomous_boq_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(autoplanning.autonomous_boq_engine, proj)


class TownshipIn(BaseModel):
    township_area_sqm: Optional[float] = 50000.0
    is_mixed_use: Optional[bool] = True


@api.post("/projects/{project_id}/township-plan")
async def township_plan_route(project_id: str, body: TownshipIn, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(autoplanning.township_mixed_use_plan, proj, body.dict())


# --------------------------------------------------------------------------- Smart City Platform
@api.get("/projects/{project_id}/smart-city/traffic")
async def smart_city_traffic_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(smartcitylib.simulate_traffic, proj)


@api.get("/projects/{project_id}/smart-city/utilities")
async def smart_city_utilities_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(smartcitylib.optimize_utility_network, proj)


@api.get("/projects/{project_id}/smart-city/digital-twin")
async def smart_city_twin_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(smartcitylib.urban_digital_twin, proj)


@api.get("/projects/{project_id}/smart-city/infrastructure-forecast")
async def smart_city_infra_forecast_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(smartcitylib.forecast_infrastructure_demand, proj)


# --------------------------------------------------------------------------- Digital Twin & Smart Construction
@api.get("/projects/{project_id}/digital-twin/summary")
async def digital_twin_summary_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    iot = await run_in_threadpool(digitaltwinlib.iot_registry, proj)
    prog = await run_in_threadpool(digitaltwinlib.track_progress_4d, proj)
    qs = await run_in_threadpool(digitaltwinlib.quality_safety_audit, proj)
    fm = await run_in_threadpool(digitaltwinlib.facility_management, proj)
    return {
        "ok": True,
        "iot": iot,
        "progress_4d": prog,
        "quality_safety": qs,
        "facility_management": fm,
    }


@api.get("/projects/{project_id}/digital-twin/sensors")
async def digital_twin_sensors_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    reg = await run_in_threadpool(digitaltwinlib.iot_registry, proj)
    telemetry = await run_in_threadpool(digitaltwinlib.sensor_telemetry, proj)
    return {"ok": True, "registry": reg, "live_telemetry": telemetry}


@api.get("/projects/{project_id}/digital-twin/progress-4d")
async def digital_twin_progress_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(digitaltwinlib.track_progress_4d, proj)


@api.get("/projects/{project_id}/digital-twin/quality-safety")
async def digital_twin_quality_safety_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(digitaltwinlib.quality_safety_audit, proj)


@api.get("/projects/{project_id}/digital-twin/delays")
async def digital_twin_delays_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(digitaltwinlib.predict_delays, proj)


@api.get("/projects/{project_id}/digital-twin/facility-management")
async def digital_twin_facility_management_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(digitaltwinlib.facility_management, proj)


@api.get("/feature-list-pdf")
async def get_feature_list_pdf():
    pdf_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Aptimizer_Feature_List_Updated.pdf")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="Feature list PDF not found")
    with open(pdf_path, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Aptimizer_Feature_List_Updated.pdf"}
    )


# --------------------------------------------------------------------------- AI Civil Engineering OS
@api.get("/projects/{project_id}/ai-os/knowledge-graph")
async def get_knowledge_graph_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(aioslib.build_knowledge_graph, proj)


@api.get("/projects/{project_id}/ai-os/memory")
async def get_engineering_memory_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    activity = await db.activity.find({"project_id": project_id}, {"_id": 0}).sort("at", 1).to_list(1000)
    return await run_in_threadpool(aioslib.get_engineering_memory, proj, activity)


@api.get("/projects/{project_id}/ai-os/decision-log")
async def get_decision_log_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    activity = await db.activity.find({"project_id": project_id}, {"_id": 0}).sort("at", 1).to_list(1000)
    return await run_in_threadpool(aioslib.audit_decision_log, proj, activity)


@api.post("/projects/{project_id}/ai-os/sandbox")
async def simulate_sandbox_route(project_id: str, request: Request, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    return await run_in_threadpool(aioslib.simulate_decision_sandbox, proj, body)


@api.get("/projects/{project_id}/ai-os/benchmarks")
async def get_benchmarks_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(aioslib.compare_benchmarks, proj)


# --------------------------------------------------------------------------- Smart Procurement & Ecosystem
@api.get("/procurement/live-prices")
async def get_live_prices_route(metro: str = "Delhi-NCR"):
    return await run_in_threadpool(procurementlib.get_live_material_prices, metro)


@api.get("/procurement/forecast")
async def get_price_forecast_route(material: str = "steel", horizon: int = 12, metro: str = "Delhi-NCR"):
    return await run_in_threadpool(procurementlib.forecast_material_prices, material, horizon, metro)


@api.get("/procurement/suppliers")
async def get_suppliers_route():
    return await run_in_threadpool(procurementlib.get_supplier_intelligence)


@api.get("/projects/{project_id}/procurement/calendar")
async def get_procurement_calendar_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(procurementlib.get_procurement_calendar, proj)


@api.get("/projects/{project_id}/procurement/inventory")
async def get_inventory_plan_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(procurementlib.calculate_inventory_plan, proj)


@api.get("/projects/{project_id}/procurement/tender-docs")
async def get_tender_docs_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(procurementlib.generate_tender_documents, proj)


@api.get("/projects/{project_id}/procurement/govt-dossier")
async def get_govt_dossier_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(procurementlib.generate_government_approval_dossier, proj)


@api.get("/ecosystem/marketplace")
async def get_marketplace_route():
    return await run_in_threadpool(procurementlib.get_marketplace_catalog)


@api.get("/ecosystem/educational")
async def get_educational_guide_route(topic: str = "setbacks"):
    return await run_in_threadpool(procurementlib.get_educational_mode_guide, topic)


# --------------------------------------------------------------------------- Urban Intelligence & Sustainability
@api.get("/projects/{project_id}/urban/growth-value")
async def get_urban_growth_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.predict_urban_growth_and_value, proj)


@api.get("/projects/{project_id}/urban/climate-disasters")
async def get_climate_disasters_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.analyze_climate_and_disasters, proj)


@api.get("/projects/{project_id}/urban/noise-pollution")
async def get_noise_pollution_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.analyze_noise_and_pollution, proj)


@api.get("/projects/{project_id}/sustainability/green-building")
async def get_green_building_route(project_id: str, standard: str = "IGBC", user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.calculate_green_building_scorecard, proj, standard)


@api.get("/projects/{project_id}/sustainability/esg")
async def get_esg_report_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.generate_esg_report, proj)


@api.get("/projects/{project_id}/sustainability/lifecycle-cost")
async def get_lifecycle_cost_route(project_id: str, years: int = 30, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.calculate_lifecycle_cost, proj, years)


@api.get("/projects/{project_id}/executive/dashboard-kpis")
async def get_executive_kpis_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.get_executive_dashboard_kpis, proj)


@api.get("/projects/{project_id}/construction/equipment-risks")
async def get_equipment_risks_route(project_id: str, user: dict = Depends(get_current_user)):
    proj = await load_project(project_id, user)
    return await run_in_threadpool(urbansustlib.schedule_equipment_and_risks, proj)


app.include_router(api)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list({origin.strip().rstrip("/") for origin in
                        ["http://localhost:3000",
                         "http://127.0.0.1:3000",
                         os.environ.get("FRONTEND_URL") or "",
                         os.environ.get("RENDER_EXTERNAL_URL") or "",
                         *os.environ.get("CORS_ORIGINS", "").split(",")]
                        if origin and origin.strip() and origin.strip() != "*"}),
    allow_origin_regex=os.environ.get(
        "CORS_ORIGIN_REGEX",
        r"https://.*\.preview\.emergentagent\.com|http://(localhost|127\.0\.0\.1)(:\d+)?",
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The browser hides these from scripts unless exposed; downloads read the real file name.
    expose_headers=["Content-Disposition", "X-Export-Format"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
