from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "recruitment_dashboard").strip()
SERVER_HOST = "0.0.0.0" if os.getenv("PORT") else os.getenv("SERVER_HOST", "localhost").strip()
SERVER_PORT = int(os.getenv("PORT") or os.getenv("SERVER_PORT", "5173"))
CACHE_TTL_SECONDS = 20
response_cache = {}
BLOCKED_STATIC_NAMES = {
    ".env",
    ".env.example",
    "atlas.env.txt",
    "recruitment_bot.db",
    "server.py",
    "requirements.txt",
    "Procfile",
    "railway.json",
    "railway.env.example",
    "DEPLOY_RAILWAY.md",
    "runtime.txt",
}

def cache_key(name, query=None):
    return name if query is None else f"{name}:{query}"

def get_cached_response(name, query=None):
    key = cache_key(name, query)
    item = response_cache.get(key)
    if not item:
        return None
    created_at, data = item
    if time.time() - created_at > CACHE_TTL_SECONDS:
        response_cache.pop(key, None)
        return None
    return data

def set_cached_response(name, data, query=None):
    response_cache[cache_key(name, query)] = (time.time(), data)
    return data

def clear_response_cache():
    response_cache.clear()


def utc_now():
    return datetime.now(timezone.utc)


def iso_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value or ""


def doc_id(value):
    return str(value) if value is not None else ""


def mongo_id(value):
    try:
        return ObjectId(value)
    except Exception:
        raise ValueError("ID không hợp lệ.")


def parse_job_json(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def stringify_job_json(value):
    if isinstance(value, str):
        return value or "{}"
    return json.dumps(value or {}, ensure_ascii=False, indent=2)


def payload_has_image(payload):
    return bool(payload.get("hasImage") or payload.get("imageDataUrl") or payload.get("imageUrl"))

def industry_key(value):
    normalized = unicodedata.normalize("NFD", str(value or "").lower())
    return re.sub(r"\s+", " ", "".join(char for char in normalized if unicodedata.category(char) != "Mn")).strip()

def normalize_phone_key(value):
    return str(value or "").strip()

def has_valid_phone_characters(value):
    return bool(re.fullmatch(r"\d+", str(value or "").strip()))

def is_valid_vietnam_mobile(value):
    return bool(re.fullmatch(r"0(?:3|5|7|8|9)\d{8}", value or ""))

def normalize_zalo_key(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if re.match(r"^https?://", raw) else f"https://{raw}")
    host = parsed.netloc.replace("www.", "")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return f"{host}{path}" if host else raw.rstrip("/")

def normalize_email_key(value):
    return str(value or "").strip().lower()

def is_valid_email(value):
    email = value or ""
    if (
        email.count("@") != 1
        or re.search(r"\s", email)
        or not email.lower().endswith(".com")
        or ".." in email
    ):
        return False
    local, domain = email.rsplit("@", 1)
    if (
        not local
        or not domain
        or local.startswith(".")
        or local.endswith(".")
        or not re.fullmatch(r"[A-Za-z0-9._-]+", local)
        or not re.fullmatch(r"[A-Za-z0-9.-]+", domain)
    ):
        return False
    labels = domain.split(".")
    return all(label and not label.startswith("-") and not label.endswith("-") for label in labels)

def split_industries(value):
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").replace("｜", "|").replace(";", ",").split(",")
    industries = []
    seen = set()
    for item in raw_items:
        for part in str(item).split("|"):
            name = part.strip()
            key = industry_key(name)
            if name and key not in seen:
                seen.add(key)
                industries.append(name)
    return industries

def infer_industries(code, title, department, job_json, raw_text):
    combined = " ".join([
        code or "",
        title or "",
        department or "",
        str(job_json.get("job_description", "")) if isinstance(job_json, dict) else "",
        raw_text or "",
    ]).lower()
    industries = []
    if any(keyword in combined for keyword in [
        "agt",
        "giàn giáo",
        "gian giao",
        "cốt thép",
        "cot thep",
        "bê tông",
        "be tong",
        "công trường",
        "cong truong",
        "công trình",
        "cong trinh",
    ]):
        industries.append("Xây dựng")
    return industries


def badge_for_status(status):
    return {
        "Gấp": "danger",
        "Đúng tiến độ": "success",
        "Sắp chốt": "warning",
    }.get(status, "")


def badge_for_result(value):
    if value >= 8:
        return "success"
    if value == 0:
        return "warning"
    return ""


def normalize_order_payload(data):
    code = data.get("code", "").strip()
    title = data.get("title", "").strip()
    job_json = parse_job_json(data.get("jobJson", "{}"))
    explicit_industries = split_industries(data.get("industries") or data.get("department") or data.get("industry") or job_json.get("industries") or job_json.get("industry"))
    inferred_industries = infer_industries(code, title, data.get("department", ""), job_json, data.get("rawText", ""))
    if "agt" in code.lower() and "Xây dựng" in inferred_industries:
        industries = ["Xây dựng"]
    else:
        industry_keys = {industry_key(industry) for industry in inferred_industries}
        industries = inferred_industries + [industry for industry in explicit_industries if industry_key(industry) not in industry_keys]
    primary_industry = industries[0] if industries else ""
    department = primary_industry or data.get("department", "").strip()
    if not code or not title or not department:
        raise ValueError("Vui lòng nhập đủ mã đơn, vị trí và phòng ban.")

    job_json["industry"] = primary_industry
    job_json["industries"] = industries
    return {
        "code": code,
        "title": title,
        "position": data.get("position", title),
        "industry": primary_industry,
        "industries": industries,
        "department": department,
        "headcount": int(data.get("headcount") or 0),
        "status": data.get("status", "Chờ duyệt").strip() or "Chờ duyệt",
        "rawText": data.get("rawText", ""),
        "textUpFb": data.get("textUpFb", "").strip(),
        "jobJson": job_json,
        "location": job_json.get("location", data.get("location", "")),
        "salaryText": job_json.get("salary_text", data.get("salaryText", "")),
        "salaryNumber": job_json.get("salary_number", data.get("salaryNumber", "")),
        "salaryPeriod": job_json.get("salary_period", data.get("salaryPeriod", "")),
        "requirement": job_json.get("requirement", data.get("requirement", "")),
        "benefits": job_json.get("benefits", data.get("benefits", "")),
        "workingHours": job_json.get("working_hours", data.get("workingHours", "")),
        "interview": job_json.get("interview", data.get("interview", "")),
        "backFee": job_json.get("back_fee", data.get("backFee", "")),
        "hasImage": bool(data.get("hasImage")),
        "hasImageData": bool(data.get("hasImageData")),
        "imageUrl": data.get("imageUrl", ""),
        "imageDataUrl": data.get("imageDataUrl", ""),
        "postingStatus": data.get("postingStatus", "Chưa đăng"),
        "postingGroup": data.get("postingGroup", ""),
        "postingLink": data.get("postingLink", ""),
        "interactions": int(data.get("interactions") or 0),
        "updatedAt": utc_now(),
    }


def normalize_candidate_payload(data):
    full_name = data.get("fullName") or data.get("full_name") or data.get("name") or ""
    full_name = full_name.strip()
    if not full_name:
        raise ValueError("Vui lòng nhập họ tên ứng viên.")
    phone = data.get("phone", "").strip()
    if phone and not has_valid_phone_characters(phone):
        raise ValueError("SĐT ứng viên chỉ được nhập số.")
    phone_key = normalize_phone_key(phone) if phone else ""
    if phone and not phone_key:
        raise ValueError("SĐT ứng viên không hợp lệ.")
    if phone and not is_valid_vietnam_mobile(phone_key):
        raise ValueError("SĐT ứng viên không đúng định dạng. Vui lòng nhập số di động Việt Nam 10 số.")
    zalo_link = (data.get("zaloLink") or data.get("zaloUrl") or data.get("zalo") or "").strip()
    email = data.get("email", "").strip()
    email_key = normalize_email_key(email)
    if email_key and not is_valid_email(email_key):
        raise ValueError("Email ứng viên không đúng định dạng.")
    payload = {
        "fullName": full_name,
        "phone": phone,
        "email": email,
        "birthYear": data.get("birthYear") or data.get("birth_year") or "",
        "gender": data.get("gender", ""),
        "address": data.get("address", ""),
        "zaloLink": zalo_link,
        "groupLink": data.get("groupLink") or data.get("groupUrl") or data.get("facebookGroup") or data.get("sourceLink") or "",
        "role": data.get("role", "Ứng viên"),
        "stage": data.get("stage", "Chờ PV"),
        "status": data.get("status", "Đang hoạt động"),
        "note": data.get("note", ""),
        "updatedAt": utc_now(),
    }
    if phone_key:
        payload["phoneKey"] = phone_key
    zalo_key = normalize_zalo_key(zalo_link)
    if zalo_key:
        payload["zaloKey"] = zalo_key
    if email_key:
        payload["emailKey"] = email_key
    return payload


def normalize_ctv_payload(data):
    full_name = data.get("fullName") or data.get("full_name") or data.get("name") or ""
    full_name = full_name.strip()
    if not full_name:
        raise ValueError("Vui lòng nhập họ tên CTV.")
    phone = data.get("phone", "").strip()
    if not phone:
        raise ValueError("Vui lòng nhập SĐT CTV.")
    if not has_valid_phone_characters(phone):
        raise ValueError("SĐT CTV chỉ được nhập số.")
    phone_key = normalize_phone_key(phone)
    if not phone_key:
        raise ValueError("SĐT CTV không hợp lệ.")
    if not is_valid_vietnam_mobile(phone_key):
        raise ValueError("SĐT CTV không đúng định dạng. Vui lòng nhập số di động Việt Nam 10 số.")
    email = data.get("email", "").strip()
    email_key = normalize_email_key(email)
    if email_key and not is_valid_email(email_key):
        raise ValueError("Email CTV không đúng định dạng.")
    initials = data.get("initials", "").strip()
    if not initials:
        initials = "".join(part[0] for part in full_name.split()[:3]).upper()
    return {
        "fullName": full_name,
        "initials": initials,
        "phone": phone,
        "phoneKey": phone_key,
        "email": email,
        "zaloLink": (data.get("zaloLink") or data.get("zaloUrl") or data.get("zalo") or "").strip(),
        "status": data.get("status", "Đang hoạt động"),
        "note": data.get("note", ""),
        "updatedAt": utc_now(),
    }


def normalize_application_payload(data):
    order_id = data.get("orderId") or data.get("order_id")
    candidate_id = data.get("candidateId") or data.get("candidate_id")
    ctv_id = data.get("ctvId") or data.get("ctv_id")
    if not order_id or not candidate_id:
        raise ValueError("Vui lòng chọn đơn và ứng viên.")
    if not ctv_id:
        raise ValueError("Vui lòng chọn CTV.")
    return {
        "orderId": mongo_id(order_id),
        "candidateId": mongo_id(candidate_id),
        "ctvId": mongo_id(ctv_id) if ctv_id else None,
        "stage": data.get("stage", "Chờ PV"),
        "status": data.get("status", "Đang xử lý"),
        "sourceType": data.get("sourceType", "CTV" if ctv_id else "Khác"),
        "sourceNote": data.get("sourceNote", ""),
        "groupLink": data.get("groupLink") or data.get("groupUrl") or data.get("facebookGroup") or data.get("sourceLink") or "",
        "role": data.get("role", ""),
        "note": data.get("note", ""),
        "appliedAt": utc_now(),
        "interviewAt": data.get("interviewAt"),
        "interviewLink": data.get("interviewLink") or data.get("interviewUrl") or data.get("meetingLink") or "",
        "resultAt": data.get("resultAt"),
        "updatedAt": utc_now(),
    }


def build_dashboard_response(orders, stage_lookup, total_candidates, collaborators, candidates, active_collaborators, candidate_total=0):
    stage_order = ["Chờ PV", "Chờ về cty", "Hoàn thành"]
    pipeline = [
        {
            "label": stage,
            "value": stage_lookup.get(stage, 0),
            "percent": round((stage_lookup.get(stage, 0) / total_candidates) * 100) if total_candidates else 0,
        }
        for stage in stage_order
    ]
    return {
        "metrics": {
            "openOrders": len([order for order in orders if order["status"] != "Đã đóng"]),
            "urgentOrders": len([order for order in orders if order["status"] == "Gấp"]),
            "newCandidates": candidate_total,
            "interviewing": stage_lookup.get("Chờ về cty", 0),
            "activeCollaborators": active_collaborators,
            "filledRate": 0,
            "totalCandidates": total_candidates,
        },
        "orders": orders,
        "pipeline": pipeline,
        "collaborators": collaborators,
        "candidates": candidates,
    }


def get_bootstrap_data(query):
    with ThreadPoolExecutor(max_workers=4) as executor:
        dashboard_future = executor.submit(store.get_dashboard_data, query, False)
        candidates_future = executor.submit(store.list_candidates)
        applications_future = executor.submit(store.list_applications)
        ctvs_future = executor.submit(store.list_ctvs)
        dashboard = dashboard_future.result()
        return {
            "ok": True,
            "dashboard": dashboard,
            "candidates": candidates_future.result().get("candidates", []),
            "applications": applications_future.result().get("applications", []),
            "ctvs": ctvs_future.result().get("ctvs", []),
        }


class MongoStore:
    name = "mongodb"

    def __init__(self):
        if not MONGODB_URI:
            raise RuntimeError("Thiếu MONGODB_URI trong file .env")
        self.client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
        self.db = self.client[MONGODB_DB_NAME]

    def init(self):
        self.client.admin.command("ping")
        self.db.orders.create_index([("code", ASCENDING)], unique=True)
        self.db.orders.create_index([("status", ASCENDING)])
        self.db.orders.create_index([("industry", ASCENDING)])
        self.db.orders.create_index([("createdAt", DESCENDING)])
        self.backfill_candidate_unique_keys()
        self.drop_unique_index_if_present("candidates", "phone_1")
        self.create_unique_index_if_clean("candidates", "phoneKey")
        self.create_unique_index_if_clean("candidates", "zaloKey")
        self.create_unique_index_if_clean("candidates", "emailKey")
        self.db.candidates.create_index([("fullName", "text"), ("phone", "text"), ("email", "text")])
        self.backfill_ctv_unique_keys()
        self.db.ctvs.create_index([("phone", ASCENDING)], unique=True, sparse=True)
        self.create_unique_index_if_clean("ctvs", "phoneKey")
        self.db.ctvs.create_index([("fullName", "text"), ("phone", "text")])
        self.db.applications.create_index([("orderId", ASCENDING)])
        self.db.applications.create_index([("candidateId", ASCENDING)])
        self.db.applications.create_index([("ctvId", ASCENDING)])
        self.db.applications.create_index([("orderId", ASCENDING), ("stage", ASCENDING)])
        self.db.applications.create_index([("orderId", ASCENDING), ("candidateId", ASCENDING)], unique=True)

    def drop_unique_index_if_present(self, collection_name, index_name):
        index_info = self.db[collection_name].index_information().get(index_name)
        if index_info and index_info.get("unique"):
            self.db[collection_name].drop_index(index_name)

    def create_unique_index_if_clean(self, collection_name, field):
        duplicate = next(self.db[collection_name].aggregate([
            {"$match": {field: {"$nin": ["", None]}}},
            {"$group": {"_id": f"${field}", "total": {"$sum": 1}}},
            {"$match": {"total": {"$gt": 1}}},
            {"$limit": 1},
        ]), None)
        if duplicate:
            return
        self.db[collection_name].create_index([(field, ASCENDING)], unique=True, sparse=True)

    def backfill_candidate_unique_keys(self):
        for candidate in self.db.candidates.find({}, {"phone": 1, "email": 1, "zaloLink": 1, "zaloUrl": 1, "zalo": 1}):
            set_values = {}
            unset_values = {}
            phone_key = normalize_phone_key(candidate.get("phone", ""))
            email_key = normalize_email_key(candidate.get("email", ""))
            zalo_key = normalize_zalo_key(candidate.get("zaloLink") or candidate.get("zaloUrl") or candidate.get("zalo") or "")
            if phone_key:
                set_values["phoneKey"] = phone_key
            if email_key:
                set_values["emailKey"] = email_key
            else:
                unset_values["emailKey"] = ""
            if zalo_key:
                set_values["zaloKey"] = zalo_key
            else:
                unset_values["zaloKey"] = ""
            update = {}
            if set_values:
                update["$set"] = set_values
            if unset_values:
                update["$unset"] = unset_values
            if update:
                self.db.candidates.update_one({"_id": candidate["_id"]}, update)

    def backfill_ctv_unique_keys(self):
        for ctv in self.db.ctvs.find({}, {"phone": 1}):
            phone_key = normalize_phone_key(ctv.get("phone", ""))
            if phone_key:
                self.db.ctvs.update_one({"_id": ctv["_id"]}, {"$set": {"phoneKey": phone_key}})

    def assert_candidate_unique(self, payload, candidate_id=None):
        exclude_id = mongo_id(candidate_id) if candidate_id else None
        checks = []
        if payload.get("phoneKey"):
            checks.append(("phoneKey", payload["phoneKey"], "SĐT ứng viên đã tồn tại."))
        if payload.get("zaloKey"):
            checks.append(("zaloKey", payload["zaloKey"], "Link Zalo ứng viên đã tồn tại."))
        if payload.get("emailKey"):
            checks.append(("emailKey", payload["emailKey"], "Email ứng viên đã tồn tại."))
        for field, value, message in checks:
            query = {field: value}
            if exclude_id:
                query["_id"] = {"$ne": exclude_id}
            if self.db.candidates.find_one(query, {"_id": 1}):
                raise ValueError(message)

    def assert_ctv_unique(self, payload, ctv_id=None):
        exclude_id = mongo_id(ctv_id) if ctv_id else None
        checks = []
        if payload.get("phoneKey"):
            checks.append(("phoneKey", payload["phoneKey"], "CTV đã tồn tại."))
        if payload.get("email"):
            checks.append(("email", payload["email"], "CTV đã tồn tại."))
        for field, value, message in checks:
            query = {field: value}
            if exclude_id:
                query["_id"] = {"$ne": exclude_id}
            if self.db.ctvs.find_one(query, {"_id": 1}):
                raise ValueError(message)

    def order_to_api(self, order, include_heavy=True):
        application_count = order.get("applicationCount", 0)
        data = {
            "id": doc_id(order.get("_id")),
            "code": order.get("code", ""),
            "title": order.get("title", ""),
            "department": order.get("department") or order.get("industry", ""),
            "industry": order.get("industry", ""),
            "industries": order.get("industries") or split_industries(order.get("industry") or order.get("department", "")),
            "headcount": order.get("headcount", 0),
            "location": order.get("location", ""),
            "pipeline": f"{application_count} ứng viên",
            "status": order.get("status", ""),
            "badge": badge_for_status(order.get("status", "")),
            "createdAt": iso_datetime(order.get("createdAt")),
            "updatedAt": iso_datetime(order.get("updatedAt")),
            "postingStatus": order.get("postingStatus", "Chưa đăng"),
            "postingGroup": order.get("postingGroup", ""),
            "postingLink": order.get("postingLink", ""),
            "interactions": order.get("interactions", 0),
            "hasImage": bool(order.get("hasImage")),
            "hasImageData": bool(order.get("hasImageData")),
        }
        if include_heavy:
            data.update({
                "textUpFb": order.get("textUpFb", ""),
                "jobJson": stringify_job_json(order.get("jobJson", {})),
                "imageDataUrl": order.get("imageDataUrl", ""),
                "rawText": order.get("rawText", ""),
            })
        return data

    def get_application_counts_by_order(self):
        return {
            row["_id"]: row["total"]
            for row in self.db.applications.aggregate([
                {"$group": {"_id": "$orderId", "total": {"$sum": 1}}}
            ])
        }

    def get_docs_by_id(self, collection_name, ids):
        clean_ids = [item_id for item_id in ids if item_id]
        if not clean_ids:
            return {}
        return {
            item["_id"]: item
            for item in self.db[collection_name].find({"_id": {"$in": clean_ids}})
        }

    def get_application_stats_by_ctv(self):
        stats = {}
        for row in self.db.applications.aggregate([
            {
                "$group": {
                    "_id": "$ctvId",
                    "sent": {"$sum": 1},
                    "passed": {
                        "$sum": {
                            "$cond": [{"$eq": ["$stage", "Hoàn thành"]}, 1, 0]
                        }
                    },
                }
            }
        ]):
            stats[row["_id"]] = {"sent": row["sent"], "passed": row["passed"]}
        return stats

    def get_dashboard_data(self, query, include_heavy=True):
        search = query.get("search", [""])[0].strip()
        status = query.get("status", ["all"])[0]
        filter_query = {}
        if status != "all":
            filter_query["status"] = status
        if search:
            filter_query["$or"] = [
                {"code": {"$regex": search, "$options": "i"}},
                {"title": {"$regex": search, "$options": "i"}},
                {"department": {"$regex": search, "$options": "i"}},
                {"industry": {"$regex": search, "$options": "i"}},
                {"industries": {"$regex": search, "$options": "i"}},
                {"status": {"$regex": search, "$options": "i"}},
            ]

        projection = None
        if not include_heavy:
            projection = {
                "imageDataUrl": 0,
                "rawText": 0,
                "textUpFb": 0,
                "jobJson": 0,
            }
        order_docs = list(self.db.orders.find(filter_query, projection).sort("createdAt", DESCENDING))
        application_counts = self.get_application_counts_by_order()
        orders = []
        for order in order_docs:
            order["applicationCount"] = application_counts.get(order["_id"], 0)
            orders.append(self.order_to_api(order, include_heavy))
        stage_order = ["Chờ PV", "Chờ về cty", "Hoàn thành"]
        stage_lookup = {stage: 0 for stage in stage_order}
        for row in self.db.applications.aggregate([{"$group": {"_id": "$stage", "total": {"$sum": 1}}}]):
            stage_lookup[row["_id"] or "Chờ PV"] = row["total"]
        application_total = sum(stage_lookup.values())
        candidate_total = self.db.candidates.count_documents({})
        total_candidates = application_total or candidate_total

        application_docs = list(self.db.applications.find().sort("createdAt", DESCENDING))
        candidate_lookup = self.get_docs_by_id("candidates", [app.get("candidateId") for app in application_docs])
        ctv_lookup = self.get_docs_by_id("ctvs", [app.get("ctvId") for app in application_docs])
        candidates = {stage: [] for stage in ["Chờ PV", "Chờ về cty", "Hoàn thành"]}
        for app in application_docs:
            stage = app.get("stage") if app.get("stage") in candidates else "Chờ PV"
            candidate = candidate_lookup.get(app.get("candidateId"), {})
            ctv = ctv_lookup.get(app.get("ctvId"), {})
            candidates[stage].append({
                "name": candidate.get("fullName", "Chưa có tên"),
                "role": app.get("role") or candidate.get("role") or "Ứng viên",
                "source": ctv.get("fullName") or app.get("sourceNote") or "Chưa có nguồn",
            })
        if application_total == 0 and candidate_total > 0:
            stage_lookup["Chờ PV"] = candidate_total
            for candidate in self.db.candidates.find().sort("createdAt", DESCENDING):
                candidates["Chờ PV"].append({
                    "name": candidate.get("fullName", "Chưa có tên"),
                    "role": candidate.get("role", "Ứng viên"),
                    "source": "Chưa gắn đơn/CTV",
                })

        collaborator_stats = self.get_application_stats_by_ctv()
        collaborators = []
        for ctv in self.db.ctvs.find().sort("createdAt", DESCENDING).limit(6):
            stats = collaborator_stats.get(ctv["_id"], {"sent": 0, "passed": 0})
            sent_count = stats["sent"]
            pass_count = stats["passed"]
            collaborators.append({
                "initials": ctv.get("initials", ""),
                "name": ctv.get("fullName", ""),
                "meta": f"{sent_count} ứng viên gửi",
                "result": f"{pass_count} pass",
                "badge": badge_for_result(pass_count),
            })

        active_collaborators = self.db.ctvs.count_documents({"status": {"$ne": "Ngừng hoạt động"}})
        return build_dashboard_response(orders, stage_lookup, total_candidates, collaborators, candidates, active_collaborators, candidate_total)

    def create_order(self, data):
        payload = normalize_order_payload(data)
        payload["createdAt"] = utc_now()
        existing = self.db.orders.find_one({"code": payload["code"]}, {"hasImage": 1, "imageDataUrl": 1, "imageUrl": 1})
        if existing:
            existing_has_image = bool(existing.get("hasImage") or existing.get("imageDataUrl") or existing.get("imageUrl"))
            if not existing_has_image and payload_has_image(payload):
                payload.pop("createdAt", None)
                payload["imageAddedAt"] = utc_now()
                self.db.orders.update_one({"code": payload["code"]}, {"$set": payload})
                return {"ok": True, "updatedExisting": True}
            raise ValueError("Mã đơn đã tồn tại.")
        self.db.orders.insert_one(payload)
        return {"ok": True}

    def get_order(self, code):
        order = self.db.orders.find_one({"code": code})
        if not order:
            raise ValueError("Không tìm thấy đơn.")
        order["applicationCount"] = self.db.applications.count_documents({"orderId": order["_id"]})
        return {"ok": True, "order": self.order_to_api(order)}

    def update_order(self, original_code, data):
        result = self.db.orders.update_one({"code": original_code}, {"$set": normalize_order_payload(data)})
        if result.matched_count == 0:
            raise ValueError("Không tìm thấy đơn cần cập nhật.")
        return {"ok": True}

    def delete_order(self, code):
        result = self.db.orders.delete_one({"code": code})
        if result.deleted_count == 0:
            raise ValueError("Không tìm thấy đơn cần xóa.")
        return {"ok": True}

    def list_candidates(self):
        return {"ok": True, "candidates": [
            {
                "id": doc_id(item.get("_id")),
                "fullName": item.get("fullName", ""),
                "phone": item.get("phone", ""),
                "email": item.get("email", ""),
                "birthYear": item.get("birthYear", ""),
                "gender": item.get("gender", ""),
                "address": item.get("address", ""),
                "zaloLink": item.get("zaloLink") or item.get("zaloUrl") or item.get("zalo") or "",
                "groupLink": item.get("groupLink") or item.get("groupUrl") or item.get("facebookGroup") or item.get("sourceLink") or "",
                "role": item.get("role", "Ứng viên"),
                "stage": item.get("stage", "Chờ PV"),
                "status": item.get("status", ""),
                "note": item.get("note", ""),
                "createdAt": iso_datetime(item.get("createdAt")),
                "updatedAt": iso_datetime(item.get("updatedAt")),
            }
            for item in self.db.candidates.find().sort("createdAt", DESCENDING)
        ]}

    def create_candidate(self, data):
        payload = normalize_candidate_payload(data)
        self.assert_candidate_unique(payload)
        payload["createdAt"] = utc_now()
        result = self.db.candidates.insert_one(payload)
        return {"ok": True, "id": doc_id(result.inserted_id)}

    def update_candidate(self, candidate_id, data):
        object_id = mongo_id(candidate_id)
        payload = normalize_candidate_payload(data)
        self.assert_candidate_unique(payload, candidate_id)
        update = {"$set": payload}
        unset_fields = {}
        if not payload.get("phoneKey"):
            unset_fields["phoneKey"] = ""
        if not payload.get("zaloKey"):
            unset_fields["zaloKey"] = ""
        if not payload.get("emailKey"):
            unset_fields["emailKey"] = ""
        if unset_fields:
            update["$unset"] = unset_fields
        result = self.db.candidates.update_one({"_id": object_id}, update)
        if result.matched_count == 0:
            raise ValueError("Không tìm thấy ứng viên.")
        return {"ok": True}

    def delete_candidate(self, candidate_id):
        result = self.db.candidates.delete_one({"_id": mongo_id(candidate_id)})
        if result.deleted_count == 0:
            raise ValueError("Không tìm thấy ứng viên.")
        return {"ok": True}

    def list_ctvs(self):
        collaborator_stats = self.get_application_stats_by_ctv()
        return {"ok": True, "ctvs": [
            {
                "id": doc_id(item.get("_id")),
                "fullName": item.get("fullName", ""),
                "initials": item.get("initials", ""),
                "phone": item.get("phone", ""),
                "email": item.get("email", ""),
                "zaloLink": item.get("zaloLink") or item.get("zaloUrl") or item.get("zalo") or "",
                "recruitedCount": collaborator_stats.get(item.get("_id"), {}).get("passed", 0),
                "status": item.get("status", ""),
                "note": item.get("note", ""),
                "createdAt": iso_datetime(item.get("createdAt")),
                "updatedAt": iso_datetime(item.get("updatedAt")),
            }
            for item in self.db.ctvs.find().sort("createdAt", DESCENDING)
        ]}

    def create_ctv(self, data):
        payload = normalize_ctv_payload(data)
        self.assert_ctv_unique(payload)
        payload["createdAt"] = utc_now()
        result = self.db.ctvs.insert_one(payload)
        return {"ok": True, "id": doc_id(result.inserted_id)}

    def update_ctv(self, ctv_id, data):
        payload = normalize_ctv_payload(data)
        self.assert_ctv_unique(payload, ctv_id)
        result = self.db.ctvs.update_one({"_id": mongo_id(ctv_id)}, {"$set": payload})
        if result.matched_count == 0:
            raise ValueError("Không tìm thấy CTV.")
        return {"ok": True}

    def delete_ctv(self, ctv_id):
        result = self.db.ctvs.delete_one({"_id": mongo_id(ctv_id)})
        if result.deleted_count == 0:
            raise ValueError("Không tìm thấy CTV.")
        return {"ok": True}

    def list_applications(self):
        applications = []
        application_docs = list(self.db.applications.find().sort("createdAt", DESCENDING))
        order_lookup = self.get_docs_by_id("orders", [item.get("orderId") for item in application_docs])
        candidate_lookup = self.get_docs_by_id("candidates", [item.get("candidateId") for item in application_docs])
        ctv_lookup = self.get_docs_by_id("ctvs", [item.get("ctvId") for item in application_docs])
        for item in application_docs:
            order = order_lookup.get(item.get("orderId"), {})
            candidate = candidate_lookup.get(item.get("candidateId"), {})
            ctv = ctv_lookup.get(item.get("ctvId"), {})
            applications.append({
                "id": doc_id(item.get("_id")),
                "orderId": doc_id(item.get("orderId")),
                "orderCode": order.get("code", ""),
                "orderTitle": order.get("title", ""),
                "candidateId": doc_id(item.get("candidateId")),
                "candidateName": candidate.get("fullName", ""),
                "ctvId": doc_id(item.get("ctvId")),
                "ctvName": ctv.get("fullName", ""),
                "ctvZaloLink": ctv.get("zaloLink") or (f"https://zalo.me/{''.join(ch for ch in str(ctv.get('phone', '')) if ch.isdigit())}" if ctv.get("phone") else ""),
                "stage": item.get("stage", ""),
                "status": item.get("status", ""),
                "sourceType": item.get("sourceType", ""),
                "sourceNote": item.get("sourceNote", ""),
                "groupLink": item.get("groupLink") or item.get("groupUrl") or item.get("facebookGroup") or item.get("sourceLink") or "",
                "role": item.get("role", ""),
                "note": item.get("note", ""),
                "appliedAt": iso_datetime(item.get("appliedAt")),
                "interviewAt": iso_datetime(item.get("interviewAt")),
                "interviewLink": item.get("interviewLink") or item.get("interviewUrl") or item.get("meetingLink") or "",
                "resultAt": iso_datetime(item.get("resultAt")),
                "createdAt": iso_datetime(item.get("createdAt")),
                "updatedAt": iso_datetime(item.get("updatedAt")),
            })
        return {"ok": True, "applications": applications}

    def create_application(self, data):
        payload = normalize_application_payload(data)
        payload["createdAt"] = utc_now()
        result = self.db.applications.insert_one(payload)
        return {"ok": True, "id": doc_id(result.inserted_id)}

    def update_application(self, application_id, data):
        object_id = mongo_id(application_id)
        existing = self.db.applications.find_one({"_id": object_id}) or {}
        payload = normalize_application_payload(data)
        if not data.get("appliedAt") and existing.get("appliedAt"):
            payload["appliedAt"] = existing.get("appliedAt")
        result = self.db.applications.update_one({"_id": object_id}, {"$set": payload})
        if result.matched_count == 0:
            raise ValueError("Không tìm thấy lượt ứng tuyển.")
        return {"ok": True}

    def delete_application(self, application_id):
        result = self.db.applications.delete_one({"_id": mongo_id(application_id)})
        if result.deleted_count == 0:
            raise ValueError("Không tìm thấy lượt ứng tuyển.")
        return {"ok": True}


store = MongoStore()


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def send_json(self, data, status_code=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        requested_name = Path(unquote(parsed.path)).name
        if requested_name in BLOCKED_STATIC_NAMES or requested_name.endswith((".py", ".db")):
            self.send_error(404)
            return
        if parsed.path == "/api/bootstrap":
            cached = get_cached_response("bootstrap", parsed.query)
            self.send_json(cached or set_cached_response("bootstrap", get_bootstrap_data(query), parsed.query))
            return
        if parsed.path == "/api/dashboard":
            cached = get_cached_response("dashboard", parsed.query)
            self.send_json(cached or set_cached_response("dashboard", store.get_dashboard_data(query, False), parsed.query))
            return
        if parsed.path == "/api/order-detail":
            code = query.get("code", [""])[0]
            cached = get_cached_response("order-detail", parsed.query)
            if cached:
                self.send_json(cached)
                return
            try:
                self.send_json(set_cached_response("order-detail", store.get_order(code), parsed.query))
            except ValueError as error:
                self.send_json({"ok": False, "message": str(error)}, 404)
            return
        if parsed.path.startswith("/api/orders/"):
            code = unquote(parsed.path.removeprefix("/api/orders/"))
            try:
                self.send_json(store.get_order(code))
            except ValueError as error:
                self.send_json({"ok": False, "message": str(error)}, 404)
            return
        if parsed.path == "/api/database-status":
            self.send_json({"ok": True, "backend": store.name, "database": MONGODB_DB_NAME})
            return
        if parsed.path == "/api/candidates":
            cached = get_cached_response("candidates")
            self.send_json(cached or set_cached_response("candidates", store.list_candidates()))
            return
        if parsed.path == "/api/ctvs":
            cached = get_cached_response("ctvs")
            self.send_json(cached or set_cached_response("ctvs", store.list_ctvs()))
            return
        if parsed.path == "/api/applications":
            cached = get_cached_response("applications")
            self.send_json(cached or set_cached_response("applications", store.list_applications()))
            return
        super().do_GET()

    def do_POST(self):
        clear_response_cache()
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/orders":
                self.send_json(store.create_order(self.read_json_body()), 201)
                return
            if parsed.path == "/api/candidates":
                self.send_json(store.create_candidate(self.read_json_body()), 201)
                return
            if parsed.path == "/api/ctvs":
                self.send_json(store.create_ctv(self.read_json_body()), 201)
                return
            if parsed.path == "/api/applications":
                self.send_json(store.create_application(self.read_json_body()), 201)
                return
        except DuplicateKeyError:
            self.send_json({"ok": False, "message": "Dữ liệu đã tồn tại."}, 409)
            return
        except ValueError as error:
            self.send_json({"ok": False, "message": str(error)}, 400)
            return
        except Exception:
            self.send_json({"ok": False, "message": "Không thể lưu dữ liệu."}, 500)
            return
        self.send_error(404)

    def do_PUT(self):
        clear_response_cache()
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/orders/"):
                code = unquote(parsed.path.removeprefix("/api/orders/"))
                self.send_json(store.update_order(code, self.read_json_body()))
                return
            if parsed.path.startswith("/api/candidates/"):
                item_id = unquote(parsed.path.removeprefix("/api/candidates/"))
                self.send_json(store.update_candidate(item_id, self.read_json_body()))
                return
            if parsed.path.startswith("/api/ctvs/"):
                item_id = unquote(parsed.path.removeprefix("/api/ctvs/"))
                self.send_json(store.update_ctv(item_id, self.read_json_body()))
                return
            if parsed.path.startswith("/api/applications/"):
                item_id = unquote(parsed.path.removeprefix("/api/applications/"))
                self.send_json(store.update_application(item_id, self.read_json_body()))
                return
        except DuplicateKeyError:
            self.send_json({"ok": False, "message": "Dữ liệu đã tồn tại."}, 409)
            return
        except ValueError as error:
            self.send_json({"ok": False, "message": str(error)}, 400)
            return
        except Exception:
            self.send_json({"ok": False, "message": "Không thể cập nhật dữ liệu."}, 500)
            return
        self.send_error(404)

    def do_DELETE(self):
        clear_response_cache()
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/orders/"):
                code = unquote(parsed.path.removeprefix("/api/orders/"))
                self.send_json(store.delete_order(code))
                return
            if parsed.path.startswith("/api/candidates/"):
                item_id = unquote(parsed.path.removeprefix("/api/candidates/"))
                self.send_json(store.delete_candidate(item_id))
                return
            if parsed.path.startswith("/api/ctvs/"):
                item_id = unquote(parsed.path.removeprefix("/api/ctvs/"))
                self.send_json(store.delete_ctv(item_id))
                return
            if parsed.path.startswith("/api/applications/"):
                item_id = unquote(parsed.path.removeprefix("/api/applications/"))
                self.send_json(store.delete_application(item_id))
                return
        except ValueError as error:
            self.send_json({"ok": False, "message": str(error)}, 404)
            return
        except Exception:
            self.send_json({"ok": False, "message": "Không thể xóa dữ liệu."}, 500)
            return
        self.send_error(404)


if __name__ == "__main__":
    store.init()
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), DashboardHandler)
    print(f"Dashboard server: http://{SERVER_HOST}:{SERVER_PORT}")
    print("Database backend: mongodb")
    print(f"Database: {MONGODB_DB_NAME}")
    server.serve_forever()
