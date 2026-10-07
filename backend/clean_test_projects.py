import os
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aptimizer")

client = MongoClient(MONGO_URL)
db = client[DB_NAME]

TEST_NAMES = [
    "BIM route test",
    "Stream Smoke",
    "Route Smoke",
    "Vastu Demo",
    "Health",
    "Sync Demo",
    "Guard",
    "Persist",
    "Reliability Demo",
    "BIM Verification",
    "TEST_ENG_V2",
    "sdfghjkl",
    "adsfwedg",
    "abc",
]

to_delete = list(db.projects.find({"name": {"$in": TEST_NAMES}}))
project_ids = [p["_id"] for p in to_delete]
project_id_strs = [str(pid) for pid in project_ids]

print(f"Found {len(to_delete)} test projects to delete:")
for p in to_delete:
    print(f" - {p['name']} ({p['_id']})")

if project_ids:
    # Remove associated sub-records
    del_activity = db.activity.delete_many({"project_id": {"$in": project_ids + project_id_strs}})
    del_versions = db.versions.delete_many({"project_id": {"$in": project_ids + project_id_strs}})
    del_comments = db.comments.delete_many({"project_id": {"$in": project_ids + project_id_strs}})
    del_shares = db.shares.delete_many({"project_id": {"$in": project_ids + project_id_strs}})
    del_tasks = db.tasks.delete_many({"project_id": {"$in": project_ids + project_id_strs}})
    
    # Remove the projects themselves
    del_projects = db.projects.delete_many({"_id": {"$in": project_ids}})
    print(f"\nDeleted {del_projects.deleted_count} projects.")
    print(f"Cleaned related records: activity={del_activity.deleted_count}, versions={del_versions.deleted_count}, comments={del_comments.deleted_count}, shares={del_shares.deleted_count}, tasks={del_tasks.deleted_count}")

remaining = list(db.projects.find({}, {"name": 1, "client": 1, "created_at": 1}))
print(f"\nRemaining projects in database ({len(remaining)}):")
for r in remaining:
    print(f" - {r.get('name')} (Client: {r.get('client')})")
