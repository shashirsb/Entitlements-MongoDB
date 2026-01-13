import random
import json
from datetime import datetime
from pymongo import MongoClient
import sys

# =====================================================
# CONFIG & STATE
# =====================================================
MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "entitlement_v3_agnostic"

ROLES_PER_CLIENT = 3
GRANTS_PER_ROLE = 3
DENY_BIT = 0b1000
ALLOW_MASKS = [1, 3]

state = {
    "defn": None,
    "cid": None,
    "users": [],
    "roles": []
}

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# =====================================================
# HELPERS
# =====================================================
def check_state(*keys):
    for key in keys:
        if not state.get(key):
            print(f"⚠️ Error: '{key}' is missing. Please run prerequisite steps.")
            return False
    return True

def minify(doc):
    return json.dumps(doc, separators=(",", ":"), default=str)

def prompt(msg):
    while True:
        v = input(msg).strip()
        if v: return v

def rand_mask():
    return DENY_BIT if random.random() < 0.15 else random.choice(ALLOW_MASKS)

def rand_limit():
    return random.choice([10, 100, 1000, 10000])

def get_arrangement_keys(defn):
    return defn.get("arrangements") or defn.get("dimensionMap") or []

# =====================================================
# CORE LOGIC FUNCTIONS
# =====================================================

def setup_indexes():
    db.effective_entitlements.create_index([("clientId", 1), ("userId", 1), ("system", 1)])
    db.trace.create_index([("clientId", 1), ("userId", 1), ("system", 1)])
    db.user_roles.create_index([("clientId", 1), ("userId", 1), ("roleId", 1)], unique=True)
    db.role_entitlements.create_index([("clientId", 1), ("roleId", 1), ("system", 1)])
    print("✓ Indexes ensured")

def startup_mode():
    print("\n1. Truncate existing data\n2. Cancel")
    if prompt("Select option: ") == "1":
        for c in ["clients","users","roles","user_roles","role_entitlements",
                  "user_entitlements","effective_entitlements","trace", 
                  "client_dimensions", "arrangements"]:
            if c in db.list_collection_names(): db[c].delete_many({})
        print("🧹 Truncated all collections")

def select_dimension_definition():
    defs = list(db.dimension_definitions.find({}, {"_id": 0}))
    if not defs:
        print("❌ No definitions found. Run generateDim.py first.")
        return
    for i, d in enumerate(defs):
        print(f"[{i}] {minify(d)}")
    idx = int(prompt("\nSelect definition index: "))
    state["defn"] = defs[idx]
    print(f"✅ Selected System: {state['defn']['system']}")

# --- NEW ENTITY: DIMENSIONS MASTER DATA ---
def create_client_dimensions_master():
    if not check_state("cid", "defn"): return
    system = state["defn"]["system"]
    dim_keys = state["defn"]["client_dimensions"]
    
    print(f"Generating master client_dimensions for {system}...")
    for i in range(1, 6):
        doc = {
            "clientId": state["cid"],
            "system": system,
            "functionCode": f"FN_{i}",
            "values": {dk: f"{dk.upper()}_{i}" for dk in dim_keys}
        }
        db.client_dimensions.insert_one(doc)
    print("✅ Created 5 Dimension master records (FN_1 to FN_5)")

# --- NEW ENTITY: ARRANGEMENTS MASTER DATA ---
def create_arrangements_master():
    if not check_state("cid", "defn"): return
    arr_keys = get_arrangement_keys(state["defn"])
    if not arr_keys:
        print("ℹ️ No arrangement keys defined for this system.")
        return

    for i in range(1, 6):
        doc = {
            "clientId": state["cid"],
            "system": state["defn"]["system"],
            "values": {ak: f"ACC_{i}" for ak in arr_keys}
        }
        db.arrangements.insert_one(doc)
    print("✅ Created 5 Arrangement master records (ACC_1 to ACC_5)")

def create_client_step():
    if not check_state("defn"): return
    cid = "CLIENT_1"
    db.clients.update_one({"_id": cid}, {"$set": {"system": state["defn"]["system"], "createdAt": datetime.utcnow()}}, upsert=True)
    state["cid"] = cid
    print(f"✅ Client '{cid}' ready.")

def create_users_step():
    if not check_state("cid", "defn"): return
    users = [
        {"_id": "USER_1", "clientId": state["cid"], "system": state["defn"]["system"], "mode": "ROLE"},
        {"_id": "USER_2", "clientId": state["cid"], "system": state["defn"]["system"], "mode": "CUSTOM"},
    ]
    for u in users:
        db.users.update_one({"_id": u["_id"]}, {"$set": u}, upsert=True)
    state["users"] = users
    print(f"✅ Users created.")

def create_roles_step():
    if not check_state("cid", "defn"): return
    roles = []
    for i in range(ROLES_PER_CLIENT):
        r = {"_id": f"{state['cid']}_ROLE_{i}", "clientId": state['cid'], "system": state['defn']['system']}
        db.roles.update_one({"_id": r["_id"]}, {"$set": r}, upsert=True)
        roles.append(r)
    state["roles"] = roles
    print(f"✅ {len(roles)} Roles created.")

def assign_user_roles_step():
    if not check_state("cid", "users", "roles"): return
    for u in state["users"]:
        if u["mode"] != "ROLE": continue
        for r in random.sample(state["roles"], random.randint(1, len(state["roles"]))):
            db.user_roles.update_one(
                {"clientId": state["cid"], "userId": u["_id"], "roleId": r["_id"]},
                {"$set": {"system": state["defn"]["system"], "createdAt": datetime.utcnow()}},
                upsert=True
            )
    print("✅ Roles assigned.")

def create_role_entitlements_step():
    if not check_state("cid", "defn", "roles"): return
    
    # Fetch Master Data
    master_dims = list(db.client_dimensions.find({"clientId": state["cid"]}))
    master_arrs = list(db.arrangements.find({"clientId": state["cid"]}))
    
    if not master_dims:
        print("❌ Error: No master client_dimensions found. Run step 3 first.")
        return

    for r in state["roles"]:
        # Pick random master data for grants
        for _ in range(GRANTS_PER_ROLE):
            dim_sample = random.choice(master_dims)
            arr_sample = random.choice(master_arrs) if master_arrs else {"values": {}}
            
            grant = {
                "clientId": state["cid"], "roleId": r["_id"], "system": state["defn"]["system"],
                "function": {"code": dim_sample["functionCode"], "permissionMask": rand_mask(), "limit": rand_limit()},
                "client_dimensions": dim_sample["values"],
                "arrangements": arr_sample["values"]
            }
            db.role_entitlements.insert_one(grant)
    print("✅ Role entitlements generated using master data.")

def create_user_overrides_step():
    if not check_state("cid", "users", "defn"): return
    master_dims = list(db.client_dimensions.find({"clientId": state["cid"]}))
    master_arrs = list(db.arrangements.find({"clientId": state["cid"]}))
    
    count = 0
    for u in state["users"]:
        if u["mode"] != "CUSTOM": continue
        for _ in range(3):
            dim_sample = random.choice(master_dims)
            arr_sample = random.choice(master_arrs) if master_arrs else {"values": {}}
            
            override = {
                "clientId": state["cid"], "userId": u["_id"], "system": state["defn"]["system"],
                "function": {"code": dim_sample["functionCode"], "permissionMask": rand_mask(), "limit": rand_limit()},
                "client_dimensions": dim_sample["values"],
                "arrangements": arr_sample["values"]
            }
            db.user_entitlements.insert_one(override)
            count += 1
    print(f"✅ {count} User overrides generated.")

def materialize_step():
    if not check_state("cid", "defn"): return
    
    cid, defn = state["cid"], state["defn"]
    system = defn["system"]
    
    users = list(db.users.find({"clientId": cid, "system": system}))
    user_roles = list(db.user_roles.find({"clientId": cid, "system": system}))
    grants = list(db.role_entitlements.find({"clientId": cid, "system": system}))
    overrides = list(db.user_entitlements.find({"clientId": cid, "system": system}))

    grants_by_role = {}
    for g in grants: grants_by_role.setdefault(g["roleId"], []).append(g)
    overrides_by_user = {}
    for o in overrides: overrides_by_user.setdefault(o["userId"], []).append(o)

    for u in users:
        uid, mode = u["_id"], u["mode"]
        sources = []
        if mode == "CUSTOM":
            for o in overrides_by_user.get(uid, []): sources.append(("CUSTOM", None, o))
        else:
            for ur in user_roles:
                if ur["userId"] == uid:
                    for g in grants_by_role.get(ur["roleId"], []): sources.append(("ROLE", ur["roleId"], g))

        grouped = {}
        for src, rid, g in sources:
            key = (g["function"]["code"], tuple(sorted(g["client_dimensions"].items())), tuple(sorted(g["arrangements"].items())))
            grouped.setdefault(key, []).append((src, rid, g))

        for (fn, dims, arrs), entries in grouped.items():
            deny, mask_or, winning_limit, winner_role, trace_entries = False, 0, None, None, []
            for src, rid, g in entries:
                f = g["function"]
                if f["permissionMask"] & DENY_BIT: deny = True
                mask_or |= f["permissionMask"]
                if mode == "CUSTOM": winning_limit = f["limit"]
                else:
                    if winning_limit is None or f["limit"] < winning_limit:
                        winning_limit, winner_role = f["limit"], rid
                trace_entries.append({"source": src, "roleId": rid, "mask": f["permissionMask"], "limit": f["limit"]})

            effective = {
                "clientId": cid, "userId": uid, "system": system, "functionCode": fn, "sourceMode": mode,
                "effectiveMask": DENY_BIT if deny else mask_or, "effectiveLimit": winning_limit,
                "client_dimensions": dict(dims), "arrangements": dict(arrs), "generatedAt": datetime.utcnow()
            }
            if mode == "ROLE": effective["roleId"] = winner_role
            db.effective_entitlements.update_one(
                {"clientId": cid, "userId": uid, "system": system, "functionCode": fn, "client_dimensions": dict(dims), "arrangements": dict(arrs)},
                {"$set": effective}, upsert=True
            )
            db.trace.insert_one({
                "clientId": cid, "userId": uid, "system": system, "functionCode": fn, "client_dimensions": dict(dims), "arrangements": dict(arrs), "entries": trace_entries, "generatedAt": datetime.utcnow()
            })
    print("🚀 Materialization complete.")

def inherit_role_step():
    if not check_state("cid", "defn"): return
    new_role_id = prompt("Enter ID for new inherited role: ")
    db.roles.update_one({"_id": new_role_id}, {"$set": {"clientId": state["cid"], "system": state["defn"]["system"]}}, upsert=True)
    existing_roles = list(db.roles.find({"clientId": state["cid"], "_id": {"$ne": new_role_id}}))
    for i, r in enumerate(existing_roles): print(f"[{i}] {r['_id']}")
    choices = prompt("Enter indices to inherit (e.g. 0,1): ").split(',')
    for idx in choices:
        parent_grants = list(db.role_entitlements.find({"roleId": existing_roles[int(idx.strip())]["_id"]}))
        for g in parent_grants:
            g.pop("_id", None)
            g["roleId"] = new_role_id
            db.role_entitlements.insert_one(g)
    print(f"✅ Role {new_role_id} created with inherited grants.")

def generate_all_data():
    startup_mode()
    setup_indexes()
    select_dimension_definition()
    create_client_step()
    create_client_dimensions_master()
    create_arrangements_master()
    create_users_step()
    create_roles_step()
    assign_user_roles_step()
    create_role_entitlements_step()
    create_user_overrides_step()
    materialize_step()
    print("\n🏁 Batch Generation Complete.")

# =====================================================
# MAIN MENU
# =====================================================
def main_menu():
    options = [
        ("Clear Data / Setup Indexes", lambda: [startup_mode(), setup_indexes()]),
        ("Select Dimension Definition", select_dimension_definition),
        ("Create/Select Client", create_client_step),
        ("Create Dimensions Master Data", create_client_dimensions_master),
        ("Create Arrangements Master Data", create_arrangements_master),
        ("Create Users", create_users_step),
        ("Create Roles", create_roles_step),
        ("Assign User Roles", assign_user_roles_step),
        ("Generate Role Entitlements (from Master)", create_role_entitlements_step),
        ("Generate User Entitlements (from Master)", create_user_overrides_step),
        ("Materialize Effective Entitlements", materialize_step),
        ("GENERATE ALL DATA (Batch)", generate_all_data),
        ("Inherit Role", inherit_role_step),
        ("Exit", sys.exit)
    ]

    while True:
        print("\n--- ENTITLEMENT MANAGEMENT MENU ---")
        for i, (label, _) in enumerate(options):
            print(f"[{i:2}] {label}")
        
        curr_sys = state['defn']['system'] if state['defn'] else 'None'
        curr_cid = state['cid'] or 'None'
        print(f"\nContext: [System: {curr_sys}] [Client: {curr_cid}]")

        try:
            choice = int(prompt("Select action: "))
            if 0 <= choice < len(options):
                options[choice][1]()
            else:
                print("❌ Invalid choice.")
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main_menu()