import signal
import sys
from datetime import datetime
from pymongo import MongoClient, ReplaceOne
import collections
import time

DB_NAME = "entitlements_v2"
MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DENY_BIT = 0b1000

WATCH_COLLECTIONS = {
    "user_product_arrangement",
    "group_product_arrangement",
    "user_group_membership",
}

client = MongoClient(MONGO_URI)
db = client[DB_NAME]


# ----------------------------------------------------
# PER-USER RECOMPUTE ENGINE
# ----------------------------------------------------
def recompute_user_entitlements(client_id, user_id):
    direct = list(
        db.user_product_arrangement.find(
            {"clientId": client_id, "userId": user_id},
            {"_id": 0, "accountId": 1, "productCode": 1, "permissionMask": 1},
        )
    )

    rows = [
        {
            "clientId": client_id,
            "userId": user_id,
            "arrangementId": d["accountId"],
            "productCode": d["productCode"],
            "mask": d["permissionMask"],
            "source": "USER",
        }
        for d in direct
    ]

    memberships = list(
        db.user_group_membership.find(
            {"clientId": client_id, "userId": user_id},
            {"groupId": 1, "_id": 0},
        )
    )
    group_ids = [m["groupId"] for m in memberships]

    if group_ids:
        group_rows = list(
            db.group_product_arrangement.find(
                {"clientId": client_id, "groupId": {"$in": group_ids}},
                {
                    "_id": 0,
                    "groupId": 1,
                    "accountId": 1,
                    "productCode": 1,
                    "permissionMask": 1,
                },
            )
        )
        for g in group_rows:
            rows.append(
                {
                    "clientId": client_id,
                    "userId": user_id,
                    "arrangementId": g["accountId"],
                    "productCode": g["productCode"],
                    "mask": g["permissionMask"],
                    "source": f"GROUP:{g['groupId']}",
                }
            )

    merged = {}
    for r in rows:
        key = (r["arrangementId"], r["productCode"])
        bucket = merged.setdefault(
            key,
            {
                "clientId": client_id,
                "userId": user_id,
                "arrangementId": r["arrangementId"],
                "productCode": r["productCode"],
                "sources": [],
                "denyPresent": False,
                "maskOr": 0,
            },
        )
        bucket["sources"].append({"source": r["source"], "mask": r["mask"]})
        bucket["maskOr"] |= r["mask"]
        if r["mask"] & DENY_BIT:
            bucket["denyPresent"] = True

    ops = []
    for _, b in merged.items():
        effective = DENY_BIT if b["denyPresent"] else b["maskOr"]
        doc = {
            "clientId": b["clientId"],
            "userId": b["userId"],
            "arrangementId": b["arrangementId"],
            "productCode": b["productCode"],
            "effectiveMask": effective,
            "deny": b["denyPresent"],
            "trace": b["sources"],
            "lastUpdated": datetime.utcnow(),
        }
        ops.append(
            ReplaceOne(
                {
                    "clientId": b["clientId"],
                    "userId": b["userId"],
                    "arrangementId": b["arrangementId"],
                    "productCode": b["productCode"],
                },
                doc,
                upsert=True,
            )
        )

    if ops:
        db.effective_entitlements.bulk_write(ops)

    print(f"✔ recomputed {len(ops)} rows for user {user_id}")


# ----------------------------------------------------
# DETERMINE AFFECTED USERS (ALWAYS RETURNS SET OF TUPLES)
# ----------------------------------------------------
def users_from_change(change):
    coll = change["ns"]["coll"]
    full = change.get("fullDocument") or {}
    key = change.get("documentKey", {})

    doc = full or key
    client_id = doc.get("clientId")

    # USER DIRECT ENTITLEMENTS → single user
    if coll == "user_product_arrangement":
        user_id = doc.get("userId")
        if client_id and user_id:
            return {(client_id, user_id)}
        return set()

    # USER GROUP MEMBERSHIP → single user
    if coll == "user_group_membership":
        user_id = doc.get("userId")
        if client_id and user_id:
            return {(client_id, user_id)}
        return set()

    # GROUP ARRANGEMENT → ALL USERS IN GROUP
    if coll == "group_product_arrangement":
        group_id = doc.get("groupId")
        if not (client_id and group_id):
            return set()

        users = db.user_group_membership.find(
            {"clientId": client_id, "groupId": group_id},
            {"userId": 1, "_id": 0},
        )
        return {(client_id, u["userId"]) for u in users}

    return set()


# ----------------------------------------------------
# DB-LEVEL CHANGE STREAM LISTENER
# ----------------------------------------------------
def listen_for_changes():
    print("🚀 Listening for entitlement deltas…")

    pipeline = [
        {"$match": {
            "operationType": {"$in": ["insert", "update", "replace", "delete"]},
            "ns.coll": {"$in": list(WATCH_COLLECTIONS)},
        }}
    ]

    stream = db.watch(pipeline, full_document="updateLookup")

    def shutdown(*_):
        print("\n🛑 Stopping listener…")
        stream.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    pending = collections.defaultdict(set)
    last_flush = time.time()

    for change in stream:
        for cid_uid in users_from_change(change):
            pending[cid_uid[0]].add(cid_uid[1])

        # small debounce window
        if time.time() - last_flush > 0.3:
            for cid, uids in list(pending.items()):
                for uid in uids:
                    recompute_user_entitlements(cid, uid)
                pending[cid].clear()
            last_flush = time.time()


# ----------------------------------------------------
# MAIN
# ----------------------------------------------------
if __name__ == "__main__":
    listen_for_changes()
