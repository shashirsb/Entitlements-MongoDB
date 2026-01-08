"""
deltaEntitlement.py
===================

Authoring + direct recompute delta mutation layer
for Entitlement v3 (system-agnostic).

✔ Implements operations a–g
✔ Direct cascading recompute (no background jobs)
✔ Before / After diff
✔ Trace generation
✔ Safe upserts
✔ Multi-role conflict resolution
✔ AUTO-SWITCH user mode on role add (CUSTOM → ROLE)
"""

from datetime import datetime
from pymongo import MongoClient
import time

# =====================================================
# CONFIG
# =====================================================
MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "entitlement_v3_agnostic"
DENY_BIT = 0b1000

# =====================================================
# DB INIT
# =====================================================
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

authoring_writes = 0
recompute_timings = []

# =====================================================
# INPUT HELPERS
# =====================================================
def prompt(msg):
    v = input(msg).strip()
    if not v:
        raise ValueError("Input cannot be empty")
    return v

def prompt_int(msg):
    return int(prompt(msg))

def prompt_dict(msg):
    print(msg)
    print("Enter key=value pairs, empty line to finish")
    out = {}
    while True:
        line = input("> ").strip()
        if not line:
            break
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out

# =====================================================
# VALIDATION
# =====================================================
def assert_user(clientId, userId):
    if not db.users.find_one({"_id": userId, "clientId": clientId}):
        raise ValueError(f"User not found: {userId}")

def assert_role(clientId, roleId):
    if not db.roles.find_one({"_id": roleId, "clientId": clientId}):
        raise ValueError(f"Role not found: {roleId}")

# =====================================================
# EFFECTIVE SNAPSHOT + DIFF
# =====================================================
def fetch_effective_map(clientId, userId):
    out = {}
    for e in db.effective_entitlements.find(
        {"clientId": clientId, "userId": userId}, {"_id": 0}
    ):
        key = (
            e["functionCode"],
            tuple(sorted(
                (k, v) for k, v in e.items()
                if k not in {
                    "clientId", "userId", "system",
                    "functionCode", "effectiveMask",
                    "effectiveLimit", "generatedAt",
                    "sourceMode", "roleId"
                }
            ))
        )
        out[key] = (e["effectiveMask"], e["effectiveLimit"])
    return out

def diff_maps(before, after):
    removed = before.keys() - after.keys()
    added = after.keys() - before.keys()
    common = before.keys() & after.keys()

    if removed:
        print("\n❌ REMOVED")
        for k in removed:
            print(" -", k)

    if added:
        print("\n✅ ADDED")
        for k in added:
            print(" +", k, "=>", after[k])

    for k in common:
        if before[k] != after[k]:
            print("\n🔄 CHANGED")
            print(" *", k, ":", before[k], "→", after[k])

# =====================================================
# DIRECT RECOMPUTE ENGINE
# =====================================================
def recompute_latencies(clientId, userId):
    start = time.time()

    user = db.users.find_one({"_id": userId, "clientId": clientId})
    if not user:
        return

    mode = user["mode"]

    db.effective_entitlements.delete_many(
        {"clientId": clientId, "userId": userId}
    )

    if mode == "CUSTOM":
        sources = list(db.user_dimension_overrides.find(
            {"clientId": clientId, "userId": userId}
        ))
    else:
        roleIds = [
            r["roleId"]
            for r in db.user_roles.find(
                {"clientId": clientId, "userId": userId}
            )
        ]
        sources = list(db.role_dimension_grants.find(
            {"clientId": clientId, "roleId": {"$in": roleIds}}
        ))

    grouped = {}
    for g in sources:
        key = (
            g["function"]["code"],
            tuple(sorted(g["dimensions"].items())),
            tuple(sorted(g.get("arrangements", {}).items()))
        )
        grouped.setdefault(key, []).append(g)

    for (fn, dims, arrs), entries in grouped.items():
        deny = False
        mask_or = 0
        winning_limit = None
        winning_role = None
        trace_entries = []

        for g in entries:
            f = g["function"]
            if f["permissionMask"] & DENY_BIT:
                deny = True
            mask_or |= f["permissionMask"]

            if winning_limit is None or f["limit"] < winning_limit:
                winning_limit = f["limit"]
                winning_role = g.get("roleId")

            trace_entries.append({
                "source": mode,
                "roleId": g.get("roleId"),
                "mask": f["permissionMask"],
                "limit": f["limit"]
            })

        effective = {
            "clientId": clientId,
            "userId": userId,
            "system": entries[0]["system"],
            "functionCode": fn,
            "sourceMode": mode,
            "effectiveMask": DENY_BIT if deny else mask_or,
            "effectiveLimit": winning_limit,
            "generatedAt": datetime.utcnow(),
            **dict(dims),
            **dict(arrs)
        }

        if mode == "ROLE":
            effective["roleId"] = winning_role

        db.effective_entitlements.insert_one(effective)

        db.trace.insert_one({
            "clientId": clientId,
            "userId": userId,
            "functionCode": fn,
            "dimensions": dict(dims),
            "arrangements": dict(arrs),
            "entries": trace_entries,
            "generatedAt": datetime.utcnow()
        })

    recompute_timings.append(time.time() - start)

# =====================================================
# CASCADING WRAPPER
# =====================================================
def cascade_and_diff(clientId, impacted_users, mutation_fn):
    global authoring_writes, recompute_timings

    recompute_timings = []

    start_wall = time.time()

    before = {
        u: fetch_effective_map(clientId, u)
        for u in impacted_users
    }

    mutation_fn()
    authoring_writes += 1

    for u in impacted_users:
        recompute_latencies(clientId, u)

    after = {
        u: fetch_effective_map(clientId, u)
        for u in impacted_users
    }

    for u in impacted_users:
        print(f"\n=== DIFF FOR USER {u} ===")
        diff_maps(before[u], after[u])

    duration = time.time() - start_wall
    total = len(recompute_timings)
    mean_ms = (sum(recompute_timings) / total * 1000) if total else 0
    tps = total / duration if duration else 0

    print("\n📊 TS DETAILS")
    print("-----------------------------------")
    print(f"Authoring Writes     : {authoring_writes}")
    print(f"Users Recomputed     : {total}")
    print(f"Mean Latency (ms)    : {mean_ms:.2f}")
    print(f"Wall Time (s)        : {duration:.2f}")
    print(f"Effective TPS        : {tps:.2f}")

# =====================================================
# DELTA OPERATIONS (a–g)
# =====================================================
def add_user_to_role():
    clientId = prompt("ClientId: ")
    userId = prompt("UserId: ")
    roleId = prompt("RoleId: ")

    assert_user(clientId, userId)
    assert_role(clientId, roleId)

    user = db.users.find_one({"_id": userId, "clientId": clientId})

    def mutation():
        # AUTO SWITCH: CUSTOM → ROLE
        if user.get("mode") != "ROLE":
            db.users.update_one(
                {"_id": userId, "clientId": clientId},
                {"$set": {"mode": "ROLE", "modeChangedAt": datetime.utcnow()}}
            )
            db.user_dimension_overrides.delete_many(
                {"clientId": clientId, "userId": userId}
            )

        db.user_roles.update_one(
            {"clientId": clientId, "userId": userId, "roleId": roleId},
            {"$setOnInsert": {"createdAt": datetime.utcnow()}},
            upsert=True
        )

    cascade_and_diff(clientId, [userId], mutation)

def remove_user_from_role():
    clientId = prompt("ClientId: ")
    userId = prompt("UserId: ")
    roleId = prompt("RoleId: ")

    cascade_and_diff(
        clientId,
        [userId],
        lambda: db.user_roles.delete_one(
            {"clientId": clientId, "userId": userId, "roleId": roleId}
        )
    )

def modify_role():
    clientId = prompt("ClientId: ")
    roleId = prompt("RoleId: ")
    updates = prompt_dict("Role fields to update")

    users = [
        u["userId"]
        for u in db.user_roles.find(
            {"clientId": clientId, "roleId": roleId}
        )
    ]

    cascade_and_diff(
        clientId,
        users,
        lambda: db.roles.update_one(
            {"_id": roleId, "clientId": clientId},
            {"$set": updates}
        )
    )

def add_dimension_to_role():
    clientId = prompt("ClientId: ")
    roleId = prompt("RoleId: ")
    system = prompt("System: ")
    functionCode = prompt("FunctionCode: ")
    permissionMask = prompt_int("PermissionMask: ")
    limit = prompt_int("Limit: ")

    dimensions = prompt_dict("Dimensions")
    arrangements = prompt_dict("Arrangements")

    users = [
        u["userId"]
        for u in db.user_roles.find(
            {"clientId": clientId, "roleId": roleId}
        )
    ]

    cascade_and_diff(
        clientId,
        users,
        lambda: db.role_dimension_grants.update_one(
            {
                "clientId": clientId,
                "roleId": roleId,
                "function.code": functionCode,
                "dimensions": dimensions,
                "arrangements": arrangements
            },
            {
                "$setOnInsert": {
                    "clientId": clientId,
                    "roleId": roleId,
                    "system": system,
                    "function": {
                        "code": functionCode,
                        "permissionMask": permissionMask,
                        "limit": limit
                    },
                    "dimensions": dimensions,
                    "arrangements": arrangements,
                    "createdAt": datetime.utcnow()
                }
            },
            upsert=True
        )
    )
def add_two_roles_same_mask_diff_limit():
    clientId = prompt("ClientId: ")
    userId = prompt("UserId: ")
    roleId1 = prompt("RoleId_1: ")
    roleId2 = prompt("RoleId_2: ")

    assert_user(clientId, userId)
    assert_role(clientId, roleId1)
    assert_role(clientId, roleId2)

    user = db.users.find_one({"_id": userId, "clientId": clientId})

    def mutation():
        # Ensure ROLE mode
        if user.get("mode") != "ROLE":
            db.users.update_one(
                {"_id": userId, "clientId": clientId},
                {"$set": {"mode": "ROLE", "modeChangedAt": datetime.utcnow()}}
            )
            db.user_dimension_overrides.delete_many(
                {"clientId": clientId, "userId": userId}
            )

        # Add both roles
        for r in (roleId1, roleId2):
            db.user_roles.update_one(
                {"clientId": clientId, "userId": userId, "roleId": r},
                {"$setOnInsert": {"createdAt": datetime.utcnow()}},
                upsert=True
            )

    cascade_and_diff(clientId, [userId], mutation)

def add_dimension_to_user():
    clientId = prompt("ClientId: ")
    userId = prompt("UserId: ")
    system = prompt("System: ")
    functionCode = prompt("FunctionCode: ")
    permissionMask = prompt_int("PermissionMask: ")
    limit = prompt_int("Limit: ")

    dimensions = prompt_dict("Dimensions")
    arrangements = prompt_dict("Arrangements")

    def mutation():
        db.users.update_one(
            {"_id": userId, "clientId": clientId},
            {"$set": {"mode": "CUSTOM"}}
        )

        db.user_dimension_overrides.update_one(
            {
                "clientId": clientId,
                "userId": userId,
                "function.code": functionCode,
                "dimensions": dimensions,
                "arrangements": arrangements
            },
            {
                "$setOnInsert": {
                    "clientId": clientId,
                    "userId": userId,
                    "system": system,
                    "function": {
                        "code": functionCode,
                        "permissionMask": permissionMask,
                        "limit": limit
                    },
                    "dimensions": dimensions,
                    "arrangements": arrangements,
                    "createdAt": datetime.utcnow()
                }
            },
            upsert=True
        )

    cascade_and_diff(clientId, [userId], mutation)

# =====================================================
# MENU
# =====================================================
MENU = {
    "a": add_user_to_role,
    "b": remove_user_from_role,
    "c": modify_role,
    "d": add_dimension_to_role,
    "e": add_two_roles_same_mask_diff_limit,
    "g": add_dimension_to_user,
}

# =====================================================
# ENTRY
# =====================================================
if __name__ == "__main__":
    while True:
        print("""
========= DELTA ENTITLEMENT MENU =========
a. Add user to role
b. Remove user from role
c. Modify role
d. Add one dimension to role
e. Add two roles (same mask, different limit)
g. Add one dimension to user
q. Quit
""")
        choice = input("Select option: ").strip().lower()
        if choice == "q":
            break
        if choice in MENU:
            try:
                MENU[choice]()
            except Exception as e:
                print("❌ ERROR:", e)
        else:
            print("Invalid option")
