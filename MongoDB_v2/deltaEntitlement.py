"""
deltaEntitlementRunner_v02.py
=============================
Delta mutation + verification harness for v02 entitlement model.

Supports two execution modes:

1) background  -> entitlement_compiler_v02.py is running separately
                  Script waits briefly, then verifies results

2) direct      -> script calls recompute_user_entitlements() itself
                  No change-stream listener required

Switch mode via RECOMPUTE_MODE at top of file.
"""

import time
import threading
from datetime import datetime
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from tabulate import tabulate
import pprint

# ==========================================
# MODE SWITCH
# ==========================================
# "background"  -> rely on change-stream compiler
# "direct"      -> call recompute_user_entitlements() explicitly
RECOMPUTE_MODE = "background"   # or "direct"

if RECOMPUTE_MODE == "direct":
    # Import only when needed
    from entitlement_compiler_v02 import recompute_user_entitlements


pp = pprint.PrettyPrinter(indent=2)

# ==========================================
# CONFIG
# ==========================================
MAX_WORKERS = 8
SAMPLE_USERS = 100
DENY_BIT = 0b1000

MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "entitlements_v2"

client = MongoClient(MONGO_URI, maxPoolSize=MAX_WORKERS * 2)
db = client[DB_NAME]

lock = threading.Lock()
timings = []
authoring_writes = 0


# ==========================================
# HELPERS
# ==========================================
def hdr(title):
    print("\n" + "=" * 100)
    print(f"🧠 {title}")
    print("=" * 100)


def sec(title):
    print(f"\n▶ {title}")


def mask_str(mask):
    flags = []
    if mask & 1: flags.append("IMPM")
    if mask & 2: flags.append("FFCCX")
    if mask & 4: flags.append("FFAAPX")
    if mask & DENY_BIT: flags.append("DENY")
    return "|".join(flags) if flags else "NONE"


# ==========================================
# EFFECTIVE SNAPSHOT + DIFF (v02 schema)
# ==========================================
def fetch_effective_map(cid, uid):
    out = {}
    for e in db.effective_entitlements.find(
        {"clientId": cid, "userId": uid},
        {"arrangementId": 1, "productCode": 1, "effectiveMask": 1, "_id": 0},
    ):
        k = f"{e['arrangementId']}|{e['productCode']}"
        out[k] = e["effectiveMask"]
    return out


def print_map(title, m):
    sec(title)
    if not m:
        print("  (none)")
        return
    for k, v in sorted(m.items()):
        print(f"  {k} = {mask_str(v)}")


def diff_maps(before, after):
    sec("EFFECTIVE ENTITLEMENT DIFF")

    removed = before.keys() - after.keys()
    added = after.keys() - before.keys()
    common = before.keys() & after.keys()

    changed = [k for k in common if before[k] != after[k]]

    if removed:
        print("\n❌ REMOVED")
        for k in removed:
            print(f"   - {k}")

    if added:
        print("\n✅ ADDED")
        for k in added:
            print(f"   + {k} = {mask_str(after[k])}")

    if changed:
        print("\n🔄 CHANGED")
        for k in changed:
            print(f"   * {k}: {mask_str(before[k])} → {mask_str(after[k])}")


# ==========================================
# PICKERS
# ==========================================
def random_client():
    d = next(db.clients.aggregate([{"$sample": {"size": 1}}]), None)
    return d["clientId"] if d else None


def pick_user(cid):
    return next(db.users.aggregate([
        {"$match": {"clientId": cid, "status": "ACTIVE"}},
        {"$sample": {"size": 1}}
    ]), None)


def pick_group(cid):
    return next(db.user_groups.aggregate([
        {"$match": {"clientId": cid}},
        {"$sample": {"size": 1}}
    ]), None)


def pick_user_group(cid):
    return next(db.user_group_membership.aggregate([
        {"$match": {"clientId": cid}},
        {"$sample": {"size": 1}}
    ]), None)


def pick_group_arr(cid):
    return next(db.group_product_arrangement.aggregate([
        {"$match": {"clientId": cid}},
        {"$sample": {"size": 1}}
    ]), None)


def pick_user_arr(cid):
    return next(db.user_product_arrangement.aggregate([
        {"$match": {"clientId": cid}},
        {"$sample": {"size": 1}}
    ]), None)


def pick_product(cid):
    p = next(db.products.aggregate([
        {"$match": {"clientId": cid}},
        {"$sample": {"size": 1}}
    ]), None)
    return p["productCode"] if p else None


# ==========================================
# VERIFICATION PIPELINE
# ==========================================
def wait_for_background_compiler():
    time.sleep(0.8)


def trigger_recompute_if_direct(users):
    if RECOMPUTE_MODE != "direct":
        return
    for u in users:
        recompute_user_entitlements(u["clientId"], u["_id"])


def verify_users(users):
    start = datetime.utcnow()
    for u in users:
        before = fetch_effective_map(u["clientId"], u["_id"])

        if RECOMPUTE_MODE == "direct":
            trigger_recompute_if_direct([u])
        else:
            wait_for_background_compiler()

        after = fetch_effective_map(u["clientId"], u["_id"])

        # print_map("EFFECTIVE — BEFORE", before)
        # print_map("EFFECTIVE — AFTER", after)
        diff_maps(before, after)

    with lock:
        timings.append((datetime.utcnow() - start).total_seconds())


# ==========================================
# DELTA OPS (a–i)
# ==========================================
def delta_add_user_to_group():
    global authoring_writes
    hdr("DELTA A → ADD USER TO GROUP")

    cid = random_client()
    user = pick_user(cid)
    group = pick_group(cid)

    try:
        db.user_group_membership.insert_one(
            {"clientId": cid, "userId": user["_id"], "groupId": group["_id"]}
        )
        authoring_writes += 1
        print("✔ Membership added")
    except DuplicateKeyError:
        print("ℹ️ Already a member — NO-OP")

    verify_users([user])


def delta_remove_user_from_group():
    global authoring_writes
    hdr("DELTA B → REMOVE USER FROM GROUP")

    cid = random_client()
    m = pick_user_group(cid)
    user = db.users.find_one({"_id": m["userId"]})

    db.user_group_membership.delete_one({"_id": m["_id"]})
    authoring_writes += 1

    verify_users([user])


def delta_modify_user_group():
    global authoring_writes
    hdr("DELTA C → MODIFY USER GROUP")

    cid = random_client()
    m = pick_user_group(cid)
    new_group = pick_group(cid)
    user = db.users.find_one({"_id": m["userId"]})

    db.user_group_membership.update_one(
        {"_id": m["_id"]},
        {"$set": {"groupId": new_group["_id"]}}
    )
    authoring_writes += 1

    verify_users([user])


def delta_add_group_arrangement():
    global authoring_writes
    hdr("DELTA D → ADD GROUP ARRANGEMENT")

    cid = random_client()
    group = pick_group(cid)
    product = pick_product(cid)

    db.group_product_arrangement.insert_one({
        "clientId": cid,
        "groupId": group["_id"],
        "accountId": f"ACC_{int(time.time())}",
        "productCode": product,
        "permissionMask": 1
    })
    authoring_writes += 1

    users = [
        db.users.find_one({"_id": m["userId"]})
        for m in db.user_group_membership.find(
            {"clientId": cid, "groupId": group["_id"]}, {"userId": 1}
        )
    ]

    verify_users(users)


def delta_modify_group_arrangement():
    global authoring_writes
    hdr("DELTA E → MODIFY GROUP ARRANGEMENT")

    cid = random_client()
    ga = pick_group_arr(cid)

    db.group_product_arrangement.update_one(
        {"_id": ga["_id"]},
        {"$set": {"permissionMask": DENY_BIT}}
    )
    authoring_writes += 1

    users = [
        db.users.find_one({"_id": m["userId"]})
        for m in db.user_group_membership.find(
            {"clientId": cid, "groupId": ga["groupId"]}, {"userId": 1}
        )
    ]

    verify_users(users)


def delta_remove_group_arrangement():
    global authoring_writes
    hdr("DELTA F → REMOVE GROUP ARRANGEMENT")

    cid = random_client()
    ga = pick_group_arr(cid)

    db.group_product_arrangement.delete_one({"_id": ga["_id"]})
    authoring_writes += 1

    users = [
        db.users.find_one({"_id": m["userId"]})
        for m in db.user_group_membership.find(
            {"clientId": cid, "groupId": ga["groupId"]}, {"userId": 1}
        )
    ]

    verify_users(users)


def delta_add_user_arrangement():
    global authoring_writes
    hdr("DELTA G → ADD USER ARRANGEMENT")

    cid = random_client()
    user = pick_user(cid)
    product = pick_product(cid)

    db.user_product_arrangement.insert_one({
        "clientId": cid,
        "userId": user["_id"],
        "accountId": f"ACC_{int(time.time())}",
        "productCode": product,
        "permissionMask": 2
    })
    authoring_writes += 1

    verify_users([user])


def delta_remove_user_arrangement():
    global authoring_writes
    hdr("DELTA H → REMOVE USER ARRANGEMENT")

    cid = random_client()
    ua = pick_user_arr(cid)
    user = db.users.find_one({"_id": ua["userId"]})

    db.user_product_arrangement.delete_one({"_id": ua["_id"]})
    authoring_writes += 1

    verify_users([user])


def delta_modify_user_arrangement():
    global authoring_writes
    hdr("DELTA I → MODIFY USER ARRANGEMENT")

    cid = random_client()
    ua = pick_user_arr(cid)
    user = db.users.find_one({"_id": ua["userId"]})

    new_mask = DENY_BIT if ua["permissionMask"] != DENY_BIT else 2

    db.user_product_arrangement.update_one(
        {"_id": ua["_id"]},
        {"$set": {"permissionMask": new_mask}}
    )
    authoring_writes += 1

    verify_users([user])


# ==========================================
# BATCH (j)
# ==========================================
def execute_delta_batch():
    affected = {}
    fns = [
        delta_add_user_arrangement,
        delta_modify_group_arrangement,
        delta_add_user_to_group,
    ]
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        for f in as_completed([ex.submit(fn) for fn in fns]):
            pass  # recompute/verify inside ops


# ==========================================
# MENU
# ==========================================
def delta_menu():
    print(f"""
RECOMPUTE MODE: {RECOMPUTE_MODE.upper()}
----------------------------------------
a. Add user to group
b. Remove user from group
c. Modify user-group
d. Add group arrangement
e. Modify group arrangement
f. Remove group arrangement
g. Add arrangement to user
h. Remove arrangement from user
i. Modify arrangement to user
j. Execute delta batch
q. Quit
""")


if __name__ == "__main__":
    while True:
        delta_menu()
        d = input("Select: ").strip().lower()

        if d == "q":
            break

        timings.clear()
        authoring_writes = 0
        start = time.time()

        fn_map = {
            "a": delta_add_user_to_group,
            "b": delta_remove_user_from_group,
            "c": delta_modify_user_group,
            "d": delta_add_group_arrangement,
            "e": delta_modify_group_arrangement,
            "f": delta_remove_group_arrangement,
            "g": delta_add_user_arrangement,
            "h": delta_remove_user_arrangement,
            "i": delta_modify_user_arrangement,
            "j": execute_delta_batch,
        }

        if d in fn_map:
            fn_map[d]()

        duration = time.time() - start

        if timings:
            print(tabulate([
                ["Authoring Writes", authoring_writes],
                ["Authoring TPS", f"{authoring_writes/duration:.2f}"],
                ["Mean Verify/Compute (ms)", f"{mean(timings)*1000:.2f}"],
                ["Wall Time (s)", f"{duration:.2f}"],
            ], tablefmt="grid"))
