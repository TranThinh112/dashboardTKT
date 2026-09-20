import json, os, subprocess, sys, time, urllib.request

BASE = "http://127.0.0.1:5199"
TEST_DB = "recruitment_dashboard_codex_test"

def call(path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        return {"httpError": e.code, "body": e.read().decode()}

env = dict(os.environ, MONGODB_DB_NAME=TEST_DB, SERVER_PORT="5199", SERVER_HOST="127.0.0.1")
env.pop("PORT", None)
proc = subprocess.Popen([sys.executable, "server.py"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/activities", timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("server did not start")

    ctv = call("/api/ctvs", {"fullName": "CTV Test", "phone": "0900000001"})
    o1 = call("/api/orders", {"code": "TEST1", "title": "Don test 1", "industries": ["Cong nghiep"]})
    o2 = call("/api/orders", {"code": "TEST2", "title": "Don test 2", "industries": ["Xay dung"]})
    cand = call("/api/candidates", {"fullName": "UV TEST", "phone": "0900000002", "role": "Cong nghiep", "stage": "Cho PV"})
    orders = {o["code"]: o["id"] for o in call("/api/order-options")["orders"] if o["code"] in ("TEST1", "TEST2")}
    print("orders:", orders, "candidate:", cand)

    app = call("/api/applications", {
        "orderId": orders["TEST1"], "candidateId": cand["id"], "ctvId": ctv["id"],
        "suppressActivity": True, "stage": "Cho PV", "role": "Cong nghiep",
        "sourceNote": "CTV Test", "groupLink": "", "note": "",
    })
    print("created app:", app)

    res = call("/api/applications/" + app["id"], {
        "orderId": orders["TEST2"], "candidateId": cand["id"], "ctvId": ctv["id"],
        "suppressActivity": True, "stage": "Cho PV", "role": "Xay dung",
        "sourceNote": "CTV Test", "groupLink": "", "note": "",
    }, method="PUT")
    print("order change result:", res)
    call("/api/candidates/" + cand["id"], {"fullName": "UV TEST", "phone": "0900000002", "role": "Xay dung", "stage": "Cho PV"})

    for a in call("/api/activities")["activities"]:
        print("  LEDGER:", a["category"], "|", a["message"])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    from pymongo import MongoClient
    from dotenv import load_dotenv
    load_dotenv()
    MongoClient(os.environ["MONGODB_URI"]).drop_database(TEST_DB)
    print("dropped test db")
