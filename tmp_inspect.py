import os, json
from pymongo import MongoClient, DESCENDING
from dotenv import load_dotenv
load_dotenv()
c = MongoClient(os.environ["MONGODB_URI"])
db = c[os.environ["MONGODB_DB_NAME"]]
app = db.applications.find_one({"_id": __import__("bson").ObjectId("6aaae6551421e174a1288a3e")})
print(json.dumps(app, default=str, ensure_ascii=False, indent=1))
print("--- orders ---")
for o in db.orders.find({}, {"code": 1, "title": 1, "industries": 1, "industry": 1, "orderType": 1, "updatedAt": 1, "createdAt": 1}).sort("code", 1):
    print(o.get("code"), "|", o.get("title"), "|", o.get("industries") or o.get("industry"), "| upd:", o.get("updatedAt"), "| cr:", o.get("createdAt"))
print("--- activities >= 14:55 ---")
for a in db.activity_logs.find({"createdAt": {"$gte": __import__("datetime").datetime(2026,9,20,14,55)}}).sort("createdAt", DESCENDING):
    print(a.get("createdAt"), "|", a.get("category"), "|", a.get("message"))
