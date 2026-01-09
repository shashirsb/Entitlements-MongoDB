import random
import json
from datetime import datetime
from pymongo import MongoClient

# =====================================================
# CONFIG
# =====================================================
MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "entitlement_v3_agnostic"

ROLES_PER_CLIENT = 3
GRANTS_PER_ROLE = 3

DENY_BIT = 0b1000
ALLOW_MASKS = [1, 3]

# =====================================================
# DB INIT
# =====================================================
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# =====================================================
# HELPERS
# =====================================================
def minify(doc):
    return json.dumps(doc, separators=(",", ":"), default=str)

def prompt(msg):
    while True:
        v = input(msg).strip()
        if v:
            return v

def rand_value(name):
    return f"{name.upper()}_{random.randint(1, 5)}"

def rand_mask():
    return DENY_BIT if random.random() < 0.15 else random.choice(ALLOW_MASKS)

def rand_limit():
    return random.choice([10, 100, 1000, 10000])

def get_arrangement_keys(defn):
    return defn.get("arrangements") or defn.get("dimensionMap") or []

# =====================================================
# INDEX SETUP
# =====================================================
def setup_indexes():
    # ---- effective_entitlements ----
    db.effective_entitlements.create_index([("clientId", 1), ("userId", 1), ("system", 1)])
    db.effective_entitlements.create_index([("clientId", 1), ("userId", 1), ("functionCode", 1)])

    # ---- trace ----
    db.trace.create_index([("clientId", 1), ("userId", 1), ("system", 1)])

    # ---- user_roles ----
    db.user_roles.create_index([("clientId", 1), ("userId", 1), ("system", 1)])
    db.user_roles.create_index([("clientId", 1), ("userId", 1), ("roleId", 1)], unique=True)

    # ---- role_dimension_grants ----
    db.role_dimension_grants.create_index([("clientId", 1), ("roleId", 1), ("system", 1)])

    print("✓ Indexes ensured")

# =====================================================
# STARTUP MODE
# =====================================================
def startup_mode():
    print("\n=== STARTUP MODE ===")
    print("1. Truncate existing data (keep dimension_definitions)")
    print("2. Append\n")

    if prompt("Select option (1 or 2): ") == "1":
        for c in [
            "clients","users","roles","user_roles",
            "role_dimension_grants","user_dimension_overrides",
            "effective_entitlements","trace"
        ]:
            if c in db.list_collection_names():
                db[c].delete_many({})
        print("🧹 Truncated\n")

# =====================================================
# STEP 1 — SELECT DIMENSION DEFINITION
# =====================================================
def select_dimension_definition():
    defs = list(db.dimension_definitions.find({}, {"_id": 0}))
    if not defs:
        print("❌ No dimension definitions found. Please run generateDim.py first.")
        exit()
    print("\n=== AVAILABLE DIMENSION DEFINITIONS ===\n")
    for i, d in enumerate(defs):
        print(f"[{i}] {minify(d)}")
    return defs[int(prompt("\nSelect definition index: "))]

# =====================================================
# STEP 2 — CORE ENTITIES
# =====================================================
def create_client(system):
    cid = "CLIENT_1"
    db.clients.update_one(
        {"_id": cid},
        {"$set": {"system": system, "createdAt": datetime.utcnow()}},
        upsert=True
    )
    return cid

def create_users(cid, system):
    users = [
        {"_id": "USER_1", "clientId": cid, "system": system, "mode": "ROLE"},
        {"_id": "USER_2", "clientId": cid, "system": system, "mode": "CUSTOM"},
    ]
    for u in users:
        db.users.update_one({"_id": u["_id"]}, {"$set": u}, upsert=True)
    return users

def create_roles(cid, system):
    roles = []
    for i in range(ROLES_PER_CLIENT):
        r = {"_id": f"{cid}_ROLE_{i}", "clientId": cid, "system": system}
        db.roles.update_one({"_id": r["_id"]}, {"$set": r}, upsert=True)
        roles.append(r)
    return roles

def assign_user_roles(cid, system, users, roles):
    for u in users:
        if u["mode"] != "ROLE":
            continue
        for r in random.sample(roles, random.randint(1, len(roles))):
            db.user_roles.update_one(
                {"clientId": cid, "userId": u["_id"], "roleId": r["_id"]},
                {"$set": {"system": system, "createdAt": datetime.utcnow()}},
                upsert=True
            )

# =====================================================
# STEP 3 — ROLE DIMENSION GRANTS
# =====================================================
def create_role_dimension_grants(cid, defn, roles):
    for r in roles:
        for _ in range(GRANTS_PER_ROLE):
            grant = {
                "clientId": cid,
                "roleId": r["_id"],
                "system": defn["system"],
                "function": {
                    "code": rand_value("fn"),
                    "permissionMask": rand_mask(),
                    "limit": rand_limit()
                },
                "dimensions": {d: rand_value(d) for d in defn["dimensions"]},
                "arrangements": {a: rand_value(a) for a in get_arrangement_keys(defn)}
            }
            db.role_dimension_grants.update_one(
                {
                    "clientId": cid,
                    "roleId": r["_id"],
                    "system": defn["system"],
                    "function.code": grant["function"]["code"],
                    "dimensions": grant["dimensions"],
                    "arrangements": grant["arrangements"]
                },
                {"$set": grant},
                upsert=True
            )

# =====================================================
# STEP 4 — USER OVERRIDES (CUSTOM MODE)
# =====================================================
def create_user_overrides(cid, users, defn):
    for u in users:
        if u["mode"] != "CUSTOM":
            continue

        override = {
            "clientId": cid,
            "userId": u["_id"],
            "system": defn["system"],
            "function": {
                "code": rand_value("fn"),
                "permissionMask": rand_mask(),
                "limit": rand_limit()
            },
            "dimensions": {d: rand_value(d) for d in defn["dimensions"]},
            "arrangements": {a: rand_value(a) for a in get_arrangement_keys(defn)}
        }

        db.user_dimension_overrides.update_one(
            {
                "clientId": cid,
                "userId": u["_id"],
                "system": defn["system"],
                "function.code": override["function"]["code"],
                "dimensions": override["dimensions"],
                "arrangements": override["arrangements"]
            },
            {"$set": override},
            upsert=True
        )

# =====================================================
# STEP 5 — EFFECTIVE ENTITLEMENTS (MATERIALIZATION)
# =====================================================
def materialize_effective_entitlements(cid, defn):
    system = defn["system"]
    users = list(db.users.find({"clientId": cid, "system": system}))
    user_roles = list(db.user_roles.find({"clientId": cid, "system": system}))
    grants = list(db.role_dimension_grants.find({"clientId": cid, "system": system}))
    overrides = list(db.user_dimension_overrides.find({"clientId": cid, "system": system}))

    grants_by_role = {}
    for g in grants:
        grants_by_role.setdefault(g["roleId"], []).append(g)

    overrides_by_user = {}
    for o in overrides:
        overrides_by_user.setdefault(o["userId"], []).append(o)

    for u in users:
        uid = u["_id"]
        mode = u["mode"]

        sources = []
        if mode == "CUSTOM":
            for o in overrides_by_user.get(uid, []):
                sources.append(("CUSTOM", None, o))
        else:
            for ur in user_roles:
                if ur["userId"] == uid:
                    for g in grants_by_role.get(ur["roleId"], []):
                        sources.append(("ROLE", ur["roleId"], g))

        grouped = {}
        for src, rid, g in sources:
            key = (
                g["function"]["code"],
                tuple(sorted(g["dimensions"].items())),
                tuple(sorted(g["arrangements"].items()))
            )
            grouped.setdefault(key, []).append((src, rid, g))

        for (fn, dims, arrs), entries in grouped.items():
            deny = False
            mask_or = 0
            winning_limit = None
            winner_role = None
            trace_entries = []

            for src, rid, g in entries:
                f = g["function"]
                if f["permissionMask"] & DENY_BIT:
                    deny = True
                mask_or |= f["permissionMask"]

                if mode == "CUSTOM":
                    winning_limit = f["limit"]
                else:
                    if winning_limit is None or f["limit"] < winning_limit:
                        winning_limit = f["limit"]
                        winner_role = rid

                trace_entries.append({
                    "source": src,
                    "roleId": rid,
                    "mask": f["permissionMask"],
                    "limit": f["limit"]
                })

            effective = {
                "clientId": cid,
                "userId": uid,
                "system": system,
                "functionCode": fn,
                "sourceMode": mode,
                "effectiveMask": DENY_BIT if deny else mask_or,
                "effectiveLimit": winning_limit,
                "dimensions": dict(dims),
                "arrangements": dict(arrs),
                "generatedAt": datetime.utcnow()
            }

            if mode == "ROLE":
                effective["roleId"] = winner_role

            db.effective_entitlements.update_one(
                {
                    "clientId": cid,
                    "userId": uid,
                    "system": system,
                    "functionCode": fn,
                    "dimensions": dict(dims),
                    "arrangements": dict(arrs)
                },
                {"$set": effective},
                upsert=True
            )

            db.trace.insert_one({
                "clientId": cid,
                "userId": uid,
                "system": system,
                "functionCode": fn,
                "dimensions": dict(dims),
                "arrangements": dict(arrs),
                "entries": trace_entries,
                "generatedAt": datetime.utcnow()
            })

# =====================================================
# MAIN
# =====================================================
def generate():
    startup_mode()
    setup_indexes()

    defn = select_dimension_definition()
    system = defn["system"]
    print(f"\n▶ Selected Definition for System: {system}")

    cid = create_client(system)
    users = create_users(cid, system)
    roles = create_roles(cid, system)
    assign_user_roles(cid, system, users, roles)
    create_role_dimension_grants(cid, defn, roles)
    create_user_overrides(cid, users, defn)
    materialize_effective_entitlements(cid, defn)

    print("\n✅ Generation complete")
    print(f"System: {system}")
    print("USER_1 → ROLE mode")
    print("USER_2 → CUSTOM mode")

if __name__ == "__main__":
    generate()