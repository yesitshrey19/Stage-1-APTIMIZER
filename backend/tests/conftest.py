"""Shared fixtures for the tests that drive the API over HTTP.

There is exactly ONE TestClient per session, and every HTTP test uses it.

The reason is motor. `server.db` is an AsyncIOMotorClient created at import, and motor
binds itself to an event loop the first time it is used, then keeps that loop for good. A
TestClient entered as a context manager runs one portal with one loop and closes it on
exit -- so a second TestClient in the same process leaves motor holding a loop that no
longer exists, and every later request dies with "Event loop is closed".

That is not hypothetical: it is what happened the moment a second HTTP test module was
added. Individually both passed; together the later one failed all thirteen of its tests.
One session-scoped client means one loop, whatever order the modules run in.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient

import server


@pytest.fixture(scope="session")
def http_client():
    """The one TestClient, entered as a context manager so its loop stays open."""
    with TestClient(server.app) as client:
        yield client


@pytest.fixture(scope="session")
def mongo():
    """A sync pymongo handle for test setup, or a skip when there is no MongoDB.

    Setup uses pymongo rather than `server.db`: calling motor synchronously returns an
    un-awaited coroutine, inserts nothing, and every request then 404s on an id that was
    never real.
    """
    try:
        from pymongo import MongoClient
        client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
    except Exception as exc:
        pytest.skip(f"MongoDB not reachable: {exc}")
    db = client[os.environ["DB_NAME"]]
    yield db
    try:
        db.projects.delete_many({"client": "QA"})
    except Exception:
        pass
    client.close()


@pytest.fixture
def api_project(http_client, mongo, request):
    """An admin session and a throwaway project, cleaned up afterwards.

    Auth is bypassed by overriding the dependency rather than by logging in: these are
    route-shape tests, and access control has its own suite.
    """
    from bson import ObjectId
    from defaults import default_project

    admin = {"_id": ObjectId(), "email": "api-test@local", "role": "admin", "name": "API Test"}
    server.app.dependency_overrides[server.get_current_user] = lambda: admin

    label = getattr(request, "module", None)
    name = f"Test {getattr(label, '__name__', 'api')}"
    doc = default_project(name, "QA", "Hyderabad", "T-1", str(admin["_id"]))
    doc["owner_id"] = str(admin["_id"])
    pid = str(mongo.projects.insert_one(doc).inserted_id)

    yield http_client, pid

    try:
        mongo.projects.delete_one({"_id": server.oid(pid)})
    except Exception:
        pass
    server.app.dependency_overrides.clear()
