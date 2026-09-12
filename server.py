from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import base64
import io
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, quote, unquote, urlparse

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "recruitment_dashboard").strip()
SERVER_HOST = "0.0.0.0" if os.getenv("PORT") else os.getenv("SERVER_HOST", "localhost").strip()
SERVER_PORT = int(os.getenv("PORT") or os.getenv("SERVER_PORT", "5173"))
CACHE_TTL_SECONDS = 3600
DEBUG_API_TIMING = os.getenv("DEBUG_API_TIMING", "0") == "1"
response_cache = {}
response_cache_lock = Lock()
response_cache_key_locks = {}
cv_binary_cache = {}
cv_binary_cache_lock = Lock()
CV_BINARY_CACHE_MAX_ITEMS = 3
ORDER_LIST_PROJECTION = {
    "code": 1,
    "title": 1,
    "orderType": 1,
    "jobJson.order_type": 1,
    "jobJson.back_fee": 1,
    "department": 1,
    "industry": 1,
    "industries": 1,
    "headcount": 1,
    "location": 1,
    "backFee": 1,
    "status": 1,
    "createdAt": 1,
    "updatedAt": 1,
    "postingStatus": 1,
    "postingGroup": 1,
    "postingLink": 1,
    "interactions": 1,
    "hasImage": 1,
    "hasImageData": 1,
}
CANDIDATE_LIST_PROJECTION = {
    "fullName": 1,
    "phone": 1,
    "email": 1,
    "birthYear": 1,
    "gender": 1,
    "address": 1,
    "zaloLink": 1,
    "zaloUrl": 1,
    "zalo": 1,
    "groupLink": 1,
    "groupUrl": 1,
    "facebookGroup": 1,
    "sourceLink": 1,
    "cvFileName": 1,
    "cvLink": 1,
    "cvUrl": 1,
    "resumeLink": 1,
    "resumeUrl": 1,
    "profileLink": 1,
    "profileUrl": 1,
    "fileUrl": 1,
    "role": 1,
    "stage": 1,
    "status": 1,
    "note": 1,
    "createdAt": 1,
    "updatedAt": 1,
    "hasCvData": 1,
    "cvFiles": 1,
}
CTV_LIST_PROJECTION = {
    "fullName": 1,
    "initials": 1,
    "phone": 1,
    "email": 1,
    "zaloLink": 1,
    "zaloUrl": 1,
    "zalo": 1,
    "status": 1,
    "note": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
APPLICATION_LIST_PROJECTION = {
    "orderId": 1,
    "candidateId": 1,
    "ctvId": 1,
    "stage": 1,
    "status": 1,
    "sourceType": 1,
    "sourceNote": 1,
    "groupLink": 1,
    "groupUrl": 1,
    "facebookGroup": 1,
    "sourceLink": 1,
    "role": 1,
    "note": 1,
    "appliedAt": 1,
    "interviewAt": 1,
    "interviewLink": 1,
    "interviewUrl": 1,
    "meetingLink": 1,
    "resultAt": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
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
    with response_cache_lock:
        item = response_cache.get(key)
        if not item:
            return None
        created_at, data = item
        if time.time() - created_at > CACHE_TTL_SECONDS:
            response_cache.pop(key, None)
            return None
        return data

def set_cached_response(name, data, query=None):
    with response_cache_lock:
        response_cache[cache_key(name, query)] = (time.time(), data)
    return data

def clear_response_cache():
    with response_cache_lock:
        response_cache.clear()
        response_cache_key_locks.clear()

def cached_response(name, loader, query=None):
    started_at = time.perf_counter()
    cached = get_cached_response(name, query)
    if cached is not None:
        if DEBUG_API_TIMING:
            print(f"cache hit {cache_key(name, query)} {(time.perf_counter() - started_at) * 1000:.1f}ms", flush=True)
        return cached
    key = cache_key(name, query)
    with response_cache_lock:
        key_lock = response_cache_key_locks.setdefault(key, Lock())
    with key_lock:
        cached = get_cached_response(name, query)
        if cached is not None:
            if DEBUG_API_TIMING:
                print(f"cache hit {key} {(time.perf_counter() - started_at) * 1000:.1f}ms", flush=True)
            return cached
        data = loader()
        set_cached_response(name, data, query)
        if DEBUG_API_TIMING:
            print(f"cache miss {key} {(time.perf_counter() - started_at) * 1000:.1f}ms", flush=True)
        return data

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
    return bool(payload.get("imageDataUrl") or payload.get("imageUrl"))

def industry_key(value):
    normalized = unicodedata.normalize("NFD", str(value or "").lower())
    return re.sub(r"\s+", " ", "".join(char for char in normalized if unicodedata.category(char) != "Mn")).strip()


def order_search_key(*values):
    return industry_key(" ".join(str(value or "") for value in values))

def normalize_phone_key(value):
    return str(value or "").strip()

def has_valid_phone_characters(value):
    return bool(re.fullmatch(r"\d+", str(value or "").strip()))

def is_valid_vietnam_mobile(value):
    return bool(re.fullmatch(r"0(?:3|5|7|8|9)\d{8}", value or ""))

def is_valid_japan_mobile(value):
    return bool(re.fullmatch(r"(?:0[789]0\d{8}|81[789]0\d{8})", value or ""))

def is_valid_supported_mobile(value):
    return is_valid_vietnam_mobile(value) or is_valid_japan_mobile(value)

def vietnam_zalo_link_from_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"https://zalo.me/{digits}" if is_valid_vietnam_mobile(digits) else ""

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
        "khách sạn",
        "khach san",
        "vstn",
        "vệ sinh",
        "ve sinh",
        "dọn dẹp",
        "don dep",
    ]):
        industries.append("Khách sạn")
    if any(keyword in combined for keyword in [
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
    payload = {
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
    # A user-selected industry must override keywords left in an older pasted text.
    # Inference is only a fallback for orders without an explicit industry.
    inferred_industries = infer_industries(code, title, data.get("department", ""), job_json, data.get("rawText", ""))
    industries = explicit_industries or inferred_industries
    primary_industry = industries[0] if industries else ""
    department = primary_industry or data.get("department", "").strip()
    if not code or not title or not department:
        raise ValueError("Vui lòng nhập đủ mã đơn, vị trí và phòng ban.")

    order_type = (data.get("orderType") or job_json.get("order_type") or "Tokutei").strip() or "Tokutei"
    job_json["order_type"] = order_type
    job_json["industry"] = primary_industry
    job_json["industries"] = industries
    return {
        "code": code,
        "title": title,
        "position": data.get("position", title),
        "orderType": order_type,
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
        "hasImage": bool(data.get("imageDataUrl") or data.get("imageUrl")),
        "hasImageData": bool(data.get("imageDataUrl")),
        "imageUrl": data.get("imageUrl", ""),
        "imageDataUrl": data.get("imageDataUrl", ""),
        "postingStatus": data.get("postingStatus", "Chưa đăng"),
        "postingGroup": data.get("postingGroup", ""),
        "postingLink": data.get("postingLink", ""),
        "interactions": int(data.get("interactions") or 0),
        "updatedAt": utc_now(),
    }
    payload["searchKey"] = order_search_key(
        code, title, department, primary_industry, " ".join(industries),
        order_type, payload["location"], payload["status"],
    )
    return payload


def has_valid_cv_data_url(value):
    """Return whether a data URL contains decodable, non-empty CV content."""
    if not isinstance(value, str):
        return False
    match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", value, re.DOTALL)
    if not match:
        return False
    try:
        payload = base64.b64decode(match.group(3), validate=True) if match.group(2) else match.group(3).encode()
    except (ValueError, TypeError):
        return False
    return bool(payload)


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
    if phone and not is_valid_supported_mobile(phone_key):
        raise ValueError("SĐT ứng viên không đúng định dạng. Vui lòng nhập số di động Việt Nam hoặc Nhật.")
    zalo_link = (data.get("zaloLink") or data.get("zaloUrl") or data.get("zalo") or "").strip()
    email = data.get("email", "").strip()
    email_key = normalize_email_key(email)
    if email_key and not is_valid_email(email_key):
        raise ValueError("Email ứng viên không đúng định dạng.")
    cv_data_url = data.get("cvDataUrl") or ""
    cv_files = data.get("cvFiles") or []
    if isinstance(cv_files, str):
        try:
            cv_files = json.loads(cv_files) if cv_files else []
        except json.JSONDecodeError:
            raise ValueError("Danh sách file CV không hợp lệ.")
    if not isinstance(cv_files, list):
        raise ValueError("Danh sách file CV không hợp lệ.")
    if len(cv_files) > 20:
        raise ValueError("Mỗi CV chỉ hỗ trợ tối đa 20 ảnh.")
    normalized_cv_files = []
    for file in cv_files:
        if not isinstance(file, dict) or not has_valid_cv_data_url(file.get("dataUrl")):
            raise ValueError("File CV không hợp lệ hoặc không có nội dung.")
        normalized_cv_files.append({"name": str(file.get("name") or "cv"), "dataUrl": file["dataUrl"]})
    if normalized_cv_files and any(not item["dataUrl"].startswith("data:image/") for item in normalized_cv_files):
        raise ValueError("Khi tải nhiều file CV, chỉ được chọn ảnh.")
    if cv_data_url not in {"__KEEP_EXISTING_CV__", "__DELETE_CV__"} and cv_data_url and not has_valid_cv_data_url(cv_data_url):
        raise ValueError("File CV không hợp lệ hoặc không có nội dung.")
    payload = {
        "fullName": full_name,
        "phone": phone,
        "email": email,
        "birthYear": data.get("birthYear") or data.get("birth_year") or "",
        "gender": data.get("gender", ""),
        "address": data.get("address", ""),
        "zaloLink": zalo_link,
        "groupLink": data.get("groupLink") or data.get("groupUrl") or data.get("facebookGroup") or data.get("sourceLink") or "",
        "cvDataUrl": cv_data_url,
        "cvFileName": data.get("cvFileName") or "",
        "cvFiles": normalized_cv_files,
        "cvLink": data.get("cvLink") or data.get("cvUrl") or data.get("resumeLink") or data.get("resumeUrl") or data.get("profileLink") or data.get("profileUrl") or data.get("fileUrl") or "",
        "hasCvData": bool(normalized_cv_files) or has_valid_cv_data_url(cv_data_url),
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
    if not is_valid_supported_mobile(phone_key):
        raise ValueError("SĐT CTV không đúng định dạng. Vui lòng nhập số di động Việt Nam hoặc Nhật.")
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
        ctvs_future = executor.submit(store.list_ctvs, False)
        dashboard = dashboard_future.result()
        return {
            "ok": True,
            "dashboard": dashboard,
            "candidates": candidates_future.result().get("candidates", []),
            "applications": applications_future.result().get("applications", []),
            "ctvs": ctvs_future.result().get("ctvs", []),
        }

def prewarm_response_cache():
    query = {"status": ["all"], "search": [""]}
    query_string = "status=all&search="
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            ("metrics", None): executor.submit(store.get_metrics),
            ("dashboard", query_string): executor.submit(store.get_dashboard_data, query, False),
            ("candidates", None): executor.submit(store.list_candidates),
            ("applications", None): executor.submit(store.list_applications),
            ("ctvs", None): executor.submit(store.list_ctvs),
        }
        prewarmed = {}
        for (name, cache_query), future in futures.items():
            try:
                data = future.result()
                set_cached_response(name, data, cache_query)
                prewarmed[name] = data
                if DEBUG_API_TIMING:
                    print(f"prewarmed {cache_key(name, cache_query)}", flush=True)
            except Exception as error:
                print(f"Không thể prewarm cache {name}: {error}")
        if {"dashboard", "candidates", "applications", "ctvs"}.issubset(prewarmed):
            set_cached_response("bootstrap", {
                "ok": True,
                "dashboard": prewarmed["dashboard"],
                "candidates": prewarmed["candidates"].get("candidates", []),
                "applications": prewarmed["applications"].get("applications", []),
                "ctvs": prewarmed["ctvs"].get("ctvs", []),
            }, query_string)
            if DEBUG_API_TIMING:
                print(f"prewarmed {cache_key('bootstrap', query_string)}", flush=True)


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
        self.db.orders.create_index([("department", ASCENDING), ("createdAt", DESCENDING)])
        self.db.orders.create_index([("code", ASCENDING), ("createdAt", DESCENDING)])
        self.db.orders.create_index([("searchKey", ASCENDING)])
        self.backfill_order_search_keys()
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

    def backfill_order_search_keys(self):
        for order in self.db.orders.find({}, {"code": 1, "title": 1, "department": 1, "industry": 1, "industries": 1, "orderType": 1, "location": 1, "status": 1}):
            search_key = order_search_key(
                order.get("code"), order.get("title"), order.get("department"),
                order.get("industry"), " ".join(order.get("industries") or []),
                order.get("orderType"), order.get("location"), order.get("status"),
            )
            if order.get("searchKey") != search_key:
                self.db.orders.update_one({"_id": order["_id"]}, {"$set": {"searchKey": search_key}})

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
        for candidate in self.db.candidates.find({}, {"cvDataUrl": 1, "hasCvData": 1}):
            has_cv_data = has_valid_cv_data_url(candidate.get("cvDataUrl"))
            if candidate.get("hasCvData") != has_cv_data:
                self.db.candidates.update_one(
                    {"_id": candidate["_id"]},
                    {"$set": {"hasCvData": has_cv_data}},
                )

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
        if not checks:
            return
        query = {"$or": [{field: value} for field, value, _ in checks]}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        existing = self.db.candidates.find_one(query, {field: 1 for field, _, _ in checks})
        if not existing:
            return
        for field, value, message in checks:
            if existing.get(field) == value:
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
        # The compact list deliberately omits the large data URL. In that case,
        # use the persisted flag; detail views validate the actual image source.
        has_image_source = bool(order.get("imageDataUrl") or order.get("imageUrl"))
        has_image = has_image_source if include_heavy else bool(order.get("hasImage"))
        has_image_data = bool(order.get("imageDataUrl")) if include_heavy else bool(order.get("hasImageData"))
        data = {
            "id": doc_id(order.get("_id")),
            "code": order.get("code", ""),
            "title": order.get("title", ""),
            "orderType": order.get("orderType") or (order.get("jobJson") or {}).get("order_type") or "Tokutei",
            "department": order.get("department") or order.get("industry", ""),
            "industry": order.get("industry", ""),
            "industries": order.get("industries") or split_industries(order.get("industry") or order.get("department", "")),
            "headcount": order.get("headcount", 0),
            "location": order.get("location", ""),
            "backFee": order.get("backFee") or (order.get("jobJson") or {}).get("back_fee", ""),
            "pipeline": f"{application_count} ứng viên",
            "status": order.get("status", ""),
            "badge": badge_for_status(order.get("status", "")),
            "createdAt": iso_datetime(order.get("createdAt")),
            "updatedAt": iso_datetime(order.get("updatedAt")),
            "postingStatus": order.get("postingStatus", "Chưa đăng"),
            "postingGroup": order.get("postingGroup", ""),
            "postingLink": order.get("postingLink", ""),
            "interactions": order.get("interactions", 0),
            "hasImage": has_image,
            "hasImageData": has_image_data,
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

    def get_docs_by_id(self, collection_name, ids, projection=None):
        clean_ids = [item_id for item_id in ids if item_id]
        if not clean_ids:
            return {}
        return {
            item["_id"]: item
            for item in self.db[collection_name].find({"_id": {"$in": clean_ids}}, projection)
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
            # Treat the user query as plain text, not a MongoDB regex pattern.
            search_pattern = re.escape(search)
            normalized_search_pattern = re.escape(industry_key(search))
            filter_query["$or"] = [
                {"searchKey": {"$regex": normalized_search_pattern}},
                {"code": {"$regex": search_pattern, "$options": "i"}},
                {"title": {"$regex": search_pattern, "$options": "i"}},
                {"department": {"$regex": search_pattern, "$options": "i"}},
                {"industry": {"$regex": search_pattern, "$options": "i"}},
                {"industries": {"$regex": search_pattern, "$options": "i"}},
                {"location": {"$regex": search_pattern, "$options": "i"}},
                {"orderType": {"$regex": search_pattern, "$options": "i"}},
                {"jobJson.order_type": {"$regex": search_pattern, "$options": "i"}},
                {"jobJson.orderType": {"$regex": search_pattern, "$options": "i"}},
                {"status": {"$regex": search_pattern, "$options": "i"}},
            ]

        # The orders tab only needs one compact page.  Keep details (including images)
        # behind the individual order endpoint instead of sending them with the list.
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(50, max(1, int(query.get("pageSize", ["5"])[0])))
        except (TypeError, ValueError):
            page_size = 5
        sort_by = query.get("sortBy", ["createdAt"])[0]
        sort_field = {"createdAt": "createdAt", "industry": "department", "name": "code"}.get(sort_by, "createdAt")
        sort_direction = ASCENDING if query.get("sortDirection", ["desc"])[0] == "asc" else DESCENDING
        total_orders = self.db.orders.count_documents(filter_query)
        total_pages = max(1, (total_orders + page_size - 1) // page_size)
        page = min(page, total_pages)
        projection = None if include_heavy else ORDER_LIST_PROJECTION
        order_docs = list(self.db.orders.find(filter_query, projection).sort([(sort_field, sort_direction), ("_id", DESCENDING)]).skip((page - 1) * page_size).limit(page_size))
        page_order_ids = [order["_id"] for order in order_docs]
        application_counts = {
            row["_id"]: row["total"]
            for row in self.db.applications.aggregate([
                {"$match": {"orderId": {"$in": page_order_ids}}},
                {"$group": {"_id": "$orderId", "total": {"$sum": 1}}},
            ])
        } if page_order_ids else {}
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

        candidates = {stage: [] for stage in ["Chờ PV", "Chờ về cty", "Hoàn thành"]}
        collaborators = []
        if include_heavy:
            application_docs = list(self.db.applications.find().sort("createdAt", DESCENDING))
            candidate_lookup = self.get_docs_by_id("candidates", [app.get("candidateId") for app in application_docs])
            ctv_lookup = self.get_docs_by_id("ctvs", [app.get("ctvId") for app in application_docs])
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
        response = build_dashboard_response(orders, stage_lookup, total_candidates, collaborators, candidates, active_collaborators, candidate_total)
        # Dashboard metrics are global aggregates, independent of the five orders
        # returned for the current list page.
        response["metrics"]["openOrders"] = self.db.orders.count_documents({"status": {"$ne": "Đã đóng"}})
        response["metrics"]["urgentOrders"] = self.db.orders.count_documents({"status": "Gấp"})
        response["pagination"] = {
            "page": page,
            "pageSize": page_size,
            "total": total_orders,
            "totalPages": total_pages,
        }
        return response

    def get_metrics(self):
        def get_stage_lookup():
            stages = {stage: 0 for stage in ["Chờ PV", "Chờ về cty", "Hoàn thành"]}
            for row in self.db.applications.aggregate([{"$group": {"_id": "$stage", "total": {"$sum": 1}}}]):
                stages[row["_id"] or "Chờ PV"] = row["total"]
            return stages

        with ThreadPoolExecutor(max_workers=5) as executor:
            stage_future = executor.submit(get_stage_lookup)
            candidate_total_future = executor.submit(self.db.candidates.count_documents, {})
            active_collaborators_future = executor.submit(
                self.db.ctvs.count_documents,
                {"status": {"$ne": "Ngừng hoạt động"}},
            )
            order_status_future = executor.submit(
                lambda: list(self.db.orders.aggregate([
                    {
                        "$group": {
                            "_id": None,
                            "openOrders": {
                                "$sum": {"$cond": [{"$ne": ["$status", "Đã đóng"]}, 1, 0]}
                            },
                            "urgentOrders": {
                                "$sum": {"$cond": [{"$eq": ["$status", "Gấp"]}, 1, 0]}
                            },
                        }
                    }
                ]))
            )
            stage_lookup = stage_future.result()
            candidate_total = candidate_total_future.result()
            active_collaborators = active_collaborators_future.result()
            order_status_rows = order_status_future.result()

        order_status = order_status_rows[0] if order_status_rows else {}
        application_total = sum(stage_lookup.values())
        total_candidates = application_total or candidate_total
        return {
            "ok": True,
            "metrics": {
                "openOrders": order_status.get("openOrders", 0),
                "urgentOrders": order_status.get("urgentOrders", 0),
                "newCandidates": candidate_total,
                "interviewing": stage_lookup.get("Chờ về cty", 0),
                "activeCollaborators": active_collaborators,
                "filledRate": round((stage_lookup.get("Hoàn thành", 0) / total_candidates) * 100) if total_candidates else 0,
                "totalCandidates": total_candidates,
            },
        }

    def list_order_options(self):
        """Compact order records used only by the candidate matching UI."""
        return {"ok": True, "orders": [
            {
                "id": doc_id(item.get("_id")),
                "code": item.get("code", ""),
                "title": item.get("title", ""),
                "department": item.get("department") or item.get("industry", ""),
                "industry": item.get("industry", ""),
                "industries": item.get("industries", []),
                "orderType": item.get("orderType", ""),
            }
            for item in self.db.orders.find({}, {"code": 1, "title": 1, "department": 1, "industry": 1, "industries": 1, "orderType": 1}).sort("code", ASCENDING)
        ]}

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
        existing = self.db.orders.find_one({"code": original_code})
        if not existing:
            raise ValueError("Không tìm thấy đơn cần cập nhật.")
        payload = normalize_order_payload(data)
        if not payload.get("imageDataUrl") and existing.get("imageDataUrl"):
            payload["imageDataUrl"] = existing.get("imageDataUrl")
            payload["hasImage"] = True
            payload["hasImageData"] = bool(existing.get("hasImageData"))
        if not payload.get("textUpFb") and existing.get("textUpFb"):
            payload["textUpFb"] = existing.get("textUpFb")
        if len(payload.get("jobJson") or {}) <= 3 and existing.get("jobJson"):
            merged_job_json = dict(existing.get("jobJson") or {})
            merged_job_json.update(payload.get("jobJson") or {})
            payload["jobJson"] = merged_job_json
        result = self.db.orders.update_one({"code": original_code}, {"$set": payload})
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
                "hasCv": bool(item.get("hasCvData") or item.get("cvFiles") or item.get("cvLink") or item.get("cvUrl") or item.get("resumeLink") or item.get("resumeUrl") or item.get("profileLink") or item.get("profileUrl") or item.get("fileUrl")),
                "cvFileCount": len(item.get("cvFiles") or []) or int(bool(item.get("hasCvData"))),
                "cvFileName": item.get("cvFileName") or "",
                "cvLink": item.get("cvLink") or item.get("cvUrl") or item.get("resumeLink") or item.get("resumeUrl") or item.get("profileLink") or item.get("profileUrl") or item.get("fileUrl") or "",
                "role": item.get("role", "Ứng viên"),
                "stage": item.get("stage", "Chờ PV"),
                "status": item.get("status", ""),
                "note": item.get("note", ""),
                "createdAt": iso_datetime(item.get("createdAt")),
                "updatedAt": iso_datetime(item.get("updatedAt")),
            }
            for item in self.db.candidates.find({}, CANDIDATE_LIST_PROJECTION).sort("createdAt", DESCENDING)
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
        if payload.get("cvDataUrl") == "__KEEP_EXISTING_CV__":
            payload.pop("cvDataUrl", None)
            payload.pop("cvFileName", None)
            payload.pop("hasCvData", None)
            payload.pop("cvFiles", None)
        delete_cv = payload.get("cvDataUrl") == "__DELETE_CV__"
        if delete_cv:
            payload.pop("cvDataUrl", None)
            payload.pop("cvFileName", None)
            payload.pop("cvLink", None)
            payload["hasCvData"] = False
        update = {"$set": payload}
        unset_fields = {}
        if delete_cv:
            unset_fields.update({
                "cvDataUrl": "",
                "cvFileName": "",
                "cvLink": "",
                "cvUrl": "",
                "resumeLink": "",
                "resumeUrl": "",
                "profileLink": "",
                "profileUrl": "",
                "fileUrl": "",
                "cvFiles": "",
            })
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

    def get_candidate_cv(self, candidate_id, page=0):
        item = self.db.candidates.find_one({"_id": mongo_id(candidate_id)}, {"cvDataUrl": 1, "cvFileName": 1, "cvFiles": 1, "updatedAt": 1})
        if not item:
            raise ValueError("Không tìm thấy ứng viên.")
        files = item.get("cvFiles") or []
        if files and 0 <= page < len(files):
            data_url = files[page].get("dataUrl", "")
            file_name = files[page].get("name", f"cv-{page + 1}")
        else:
            data_url = item.get("cvDataUrl") or ""
            file_name = item.get("cvFileName") or "cv"
        if not has_valid_cv_data_url(data_url):
            raise ValueError("Ứng viên chưa có file CV hợp lệ.")
        cache_id = f"{candidate_id}:{page}:{iso_datetime(item.get('updatedAt'))}"
        with cv_binary_cache_lock:
            cached = cv_binary_cache.get(cache_id)
            if cached:
                cv_binary_cache.pop(cache_id)
                cv_binary_cache[cache_id] = cached
                return cached

        match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", data_url)
        mime_type = match.group(1) or "application/octet-stream"
        raw_data = match.group(3) or ""
        try:
            payload = base64.b64decode(raw_data, validate=True) if match.group(2) else unquote(raw_data).encode("utf-8")
        except (ValueError, TypeError) as error:
            raise ValueError("File CV không hợp lệ.") from error
        result = {
            "payload": payload,
            "mimeType": mime_type,
            "fileName": file_name,
        }
        # Keep only a tiny LRU cache to avoid repeatedly decoding large base64 CVs.
        with cv_binary_cache_lock:
            cv_binary_cache[cache_id] = result
            while len(cv_binary_cache) > CV_BINARY_CACHE_MAX_ITEMS:
                cv_binary_cache.pop(next(iter(cv_binary_cache)))
        return result

    def get_candidate_cv_download(self, candidate_id):
        item = self.db.candidates.find_one({"_id": mongo_id(candidate_id)}, {"fullName": 1, "cvDataUrl": 1, "cvFileName": 1, "cvFiles": 1})
        if not item:
            raise ValueError("Không tìm thấy ứng viên.")
        files = item.get("cvFiles") or []
        # A one-file CV (including an original PDF) is downloaded unchanged.
        if not files:
            cv = self.get_candidate_cv(candidate_id)
            # Preserve an uploaded PDF name, but use the candidate name for a
            # standalone image so all image-based CV downloads are consistent.
            if cv["mimeType"].startswith("image/"):
                extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(cv["mimeType"], ".img")
                safe_name = re.sub(r'[\\/:*?"<>|]+', "-", str(item.get("fullName") or "cv")).strip(" .-") or "cv"
                return {**cv, "downloadName": f"{safe_name}{extension}"}
            return {**cv, "downloadName": cv["fileName"]}

        pages = []
        try:
            for file in files:
                data_url = file.get("dataUrl") or ""
                if not has_valid_cv_data_url(data_url):
                    raise ValueError("Một ảnh CV không hợp lệ.")
                raw_data = data_url.split(",", 1)[1]
                image_bytes = base64.b64decode(raw_data, validate=True)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    # PDF pages must be RGB; respect an image's EXIF rotation first.
                    pages.append(ImageOps.exif_transpose(image).convert("RGB"))
            if not pages:
                raise ValueError("Ứng viên chưa có file CV hợp lệ.")
            output = io.BytesIO()
            pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=100.0)
            safe_name = re.sub(r'[\\/:*?"<>|]+', "-", str(item.get("fullName") or "cv")).strip(" .-") or "cv"
            return {"payload": output.getvalue(), "mimeType": "application/pdf", "downloadName": f"{safe_name}.pdf"}
        except (OSError, ValueError, TypeError, IndexError) as error:
            raise ValueError("Không thể ghép ảnh CV thành PDF.") from error

    def delete_candidate(self, candidate_id):
        result = self.db.candidates.delete_one({"_id": mongo_id(candidate_id)})
        if result.deleted_count == 0:
            raise ValueError("Không tìm thấy ứng viên.")
        return {"ok": True}

    def list_ctvs(self, include_stats=True):
        collaborator_stats = self.get_application_stats_by_ctv() if include_stats else {}
        return {"ok": True, "ctvs": [
            {
                "id": doc_id(item.get("_id")),
                "fullName": item.get("fullName", ""),
                "initials": item.get("initials", ""),
                "phone": item.get("phone", ""),
                "email": item.get("email", ""),
                "zaloLink": item.get("zaloLink") or item.get("zaloUrl") or item.get("zalo") or "",
                "recruitedCount": collaborator_stats.get(item.get("_id"), {}).get("passed", 0) if include_stats else 0,
                "status": item.get("status", ""),
                "note": item.get("note", ""),
                "createdAt": iso_datetime(item.get("createdAt")),
                "updatedAt": iso_datetime(item.get("updatedAt")),
            }
            for item in self.db.ctvs.find({}, CTV_LIST_PROJECTION).sort("createdAt", DESCENDING)
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
        application_docs = list(self.db.applications.find({}, APPLICATION_LIST_PROJECTION).sort("createdAt", DESCENDING))
        order_lookup = self.get_docs_by_id("orders", [item.get("orderId") for item in application_docs], {"code": 1, "title": 1})
        candidate_lookup = self.get_docs_by_id("candidates", [item.get("candidateId") for item in application_docs], {"fullName": 1})
        ctv_lookup = self.get_docs_by_id("ctvs", [item.get("ctvId") for item in application_docs], {"fullName": 1, "zaloLink": 1, "phone": 1})
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
                "ctvZaloLink": ctv.get("zaloLink") or vietnam_zalo_link_from_phone(ctv.get("phone")),
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
        payload = normalize_application_payload(data)
        if not data.get("appliedAt"):
            payload.pop("appliedAt", None)
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

    def send_binary(self, payload, mime_type, file_name="file", download=False):
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(payload)))
        disposition = "attachment" if download else "inline"
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(file_name)}")
        # The URL includes the candidate update timestamp, so a browser can safely
        # reuse an already opened CV without downloading it a second time.
        self.send_header("Cache-Control", "private, max-age=86400, immutable")
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
            self.send_json(cached_response("bootstrap", lambda: get_bootstrap_data(query), parsed.query))
            return
        if parsed.path == "/api/dashboard":
            self.send_json(cached_response("dashboard", lambda: store.get_dashboard_data(query, False), parsed.query))
            return
        if parsed.path == "/api/orders":
            self.send_json(cached_response("orders", lambda: store.get_dashboard_data(query, False), parsed.query))
            return
        if parsed.path == "/api/order-options":
            self.send_json(cached_response("order-options", store.list_order_options))
            return
        if parsed.path == "/api/metrics":
            self.send_json(cached_response("metrics", store.get_metrics))
            return
        if parsed.path == "/api/order-detail":
            code = query.get("code", [""])[0]
            try:
                self.send_json(cached_response("order-detail", lambda: store.get_order(code), parsed.query))
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
            self.send_json(cached_response("candidates", store.list_candidates))
            return
        if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/cv/download"):
            item_id = unquote(parsed.path.removeprefix("/api/candidates/").removesuffix("/cv/download"))
            try:
                cv = store.get_candidate_cv_download(item_id)
                self.send_binary(cv["payload"], cv["mimeType"], cv["downloadName"], download=True)
            except ValueError as error:
                self.send_json({"ok": False, "message": str(error)}, 404)
            return
        if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/cv"):
            item_id = unquote(parsed.path.removeprefix("/api/candidates/").removesuffix("/cv"))
            try:
                try:
                    page = max(0, int(query.get("page", ["0"])[0]))
                except (TypeError, ValueError):
                    page = 0
                cv = store.get_candidate_cv(item_id, page)
                self.send_binary(cv["payload"], cv["mimeType"], cv["fileName"])
            except ValueError as error:
                self.send_json({"ok": False, "message": str(error)}, 404)
            return
        if parsed.path == "/api/ctvs":
            self.send_json(cached_response("ctvs", store.list_ctvs))
            return
        if parsed.path == "/api/applications":
            self.send_json(cached_response("applications", store.list_applications))
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
    prewarm_response_cache()
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), DashboardHandler)
    print(f"Dashboard server: http://{SERVER_HOST}:{SERVER_PORT}")
    print("Database backend: mongodb")
    print(f"Database: {MONGODB_DB_NAME}")
    server.serve_forever()
