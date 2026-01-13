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

# --- ENTITY: DIMENSIONS MASTER DATA ---
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
        db.client_dimensions.update_one(
            {"clientId": state["cid"], "system": system, "functionCode": doc["functionCode"]},
            {"$set": doc},
            upsert=True
        )
    print(f"✅ Created 5 Dimension master records for {system}")

# --- ENTITY: ARRANGEMENTS MASTER DATA ---
def create_arrangements_master():
    if not check_state("cid", "defn"): return
    system = state["defn"]["system"]
    arr_keys = get_arrangement_keys(state["defn"])
    if not arr_keys:
        print("ℹ️ No arrangement keys defined for this system.")
        return

    for i in range(1, 6):
        doc = {
            "clientId": state["cid"],
            "system": system,
            "values": {ak: f"ACC_{i}" for ak in arr_keys}
        }
        db.arrangements.update_one(
            {"clientId": state["cid"], "system": system, "values": doc["values"]},
            {"$set": {"updatedAt": datetime.utcnow()}},
            upsert=True
        )
    print(f"✅ Created 5 Arrangement master records for {system}")

def create_client_step():
    if not check_state("defn"): return
    cid = "CLIENT_1"
    system = state["defn"]["system"]
    # Clients now track an array of systems they are active in
    db.clients.update_one(
        {"_id": cid}, 
        {
            "$set": {"createdAt": datetime.utcnow()},
            "$addToSet": {"systems": system}
        }, 
        upsert=True
    )
    state["cid"] = cid
    print(f"✅ Client '{cid}' linked to system '{system}'.")

def create_users_step():
    if not check_state("cid", "defn"): return
    system = state["defn"]["system"]
    
    # Define basic users
    user_configs = [
        {"_id": "USER_1", "mode": "ROLE"},
        {"_id": "USER_2", "mode": "CUSTOM"},
    ]
    
    for u in user_configs:
        # Use $addToSet to add system to the array without duplicates
        db.users.update_one(
            {"_id": u["_id"]},
            {
                "$set": {"clientId": state["cid"], "mode": u["mode"]},
                "$addToSet": {"systems": system}
            },
            upsert=True
        )
    
    # Refresh local state with current users
    state["users"] = list(db.users.find({"clientId": state["cid"]}))
    print(f"✅ Users updated/created. System '{system}' added to user system arrays.")

def create_roles_step():
    if not check_state("cid", "defn"): return
    system = state["defn"]["system"]
    roles = []
    for i in range(ROLES_PER_CLIENT):
        # Role IDs are system-specific to avoid collisions
        rid = f"{state['cid']}_{system}_ROLE_{i}"
        r = {"_id": rid, "clientId": state['cid'], "system": system}
        db.roles.update_one({"_id": r["_id"]}, {"$set": r}, upsert=True)
        roles.append(r)
    state["roles"] = roles
    print(f"✅ {len(roles)} Roles created for system: {system}")

def assign_user_roles_step():
    if not check_state("cid", "users", "roles"): return
    system = state["defn"]["system"]
    for u in state["users"]:
        # Only assign roles if the user is authorized for the current system
        if system not in u.get("systems", []): continue
        if u["mode"] != "ROLE": continue
        
        # Select a random subset of roles for this specific system
        selected_roles = random.sample(state["roles"], random.randint(1, len(state["roles"])))
        for r in selected_roles:
            db.user_roles.update_one(
                {"clientId": state["cid"], "userId": u["_id"], "roleId": r["_id"]},
                {"$set": {"system": system, "createdAt": datetime.utcnow()}},
                upsert=True
            )
    print(f"✅ Roles assigned for system: {system}")

def create_role_entitlements_step():
    if not check_state("cid", "defn", "roles"): return
    system = state["defn"]["system"]
    
    # Fetch Master Data for the current system
    master_dims = list(db.client_dimensions.find({"clientId": state["cid"], "system": system}))
    master_arrs = list(db.arrangements.find({"clientId": state["cid"], "system": system}))
    
    if not master_dims:
        print(f"❌ Error: No master client_dimensions found for {system}.")
        return

    for r in state["roles"]:
        for _ in range(GRANTS_PER_ROLE):
            dim_sample = random.choice(master_dims)
            arr_sample = random.choice(master_arrs) if master_arrs else {"values": {}}
            
            grant = {
                "clientId": state["cid"], "roleId": r["_id"], "system": system,
                "function": {"code": dim_sample["functionCode"], "permissionMask": rand_mask(), "limit": rand_limit()},
                "client_dimensions": dim_sample["values"],
                "arrangements": arr_sample["values"]
            }
            db.role_entitlements.insert_one(grant)
    print(f"✅ Role entitlements generated for {system}.")

def create_user_overrides_step():
    if not check_state("cid", "users", "defn"): return
    system = state["defn"]["system"]
    master_dims = list(db.client_dimensions.find({"clientId": state["cid"], "system": system}))
    master_arrs = list(db.arrangements.find({"clientId": state["cid"], "system": system}))
    
    count = 0
    for u in state["users"]:
        if system not in u.get("systems", []): continue
        if u["mode"] != "CUSTOM": continue
        for _ in range(3):
            dim_sample = random.choice(master_dims)
            arr_sample = random.choice(master_arrs) if master_arrs else {"values": {}}
            
            override = {
                "clientId": state["cid"], "userId": u["_id"], "system": system,
                "function": {"code": dim_sample["functionCode"], "permissionMask": rand_mask(), "limit": rand_limit()},
                "client_dimensions": dim_sample["values"],
                "arrangements": arr_sample["values"]
            }
            db.user_entitlements.insert_one(override)
            count += 1
    print(f"✅ {count} User overrides generated for {system}.")

def materialize_step():
    if not check_state("cid"): return
    
    cid = state["cid"]
    # We materialize based on all users for this client
    users = list(db.users.find({"clientId": cid}))

    for u in users:
        uid, mode = u["_id"], u["mode"]
        systems = u.get("systems", [])
        
        # Loop through every system the user is assigned to
        for system in systems:
            user_roles = list(db.user_roles.find({"clientId": cid, "userId": uid, "system": system}))
            role_ids = [ur["roleId"] for ur in user_roles]
            
            grants = list(db.role_entitlements.find({"roleId": {"$in": role_ids}, "system": system}))
            overrides = list(db.user_entitlements.find({"userId": uid, "system": system}))

            sources = []
            if mode == "CUSTOM":
                for o in overrides: sources.append(("CUSTOM", None, o))
            else:
                for g in grants: sources.append(("ROLE", g["roleId"], g))

            # Grouping by Function + Dimensions + Arrangements
            grouped = {}
            for src, rid, g in sources:
                key = (g["function"]["code"], 
                       tuple(sorted(g["client_dimensions"].items())), 
                       tuple(sorted(g["arrangements"].items())))
                grouped.setdefault(key, []).append((src, rid, g))

            for (fn, dims, arrs), entries in grouped.items():
                deny, mask_or, winning_limit, winner_role, trace_entries = False, 0, None, None, []
                for src, rid, g in entries:
                    f = g["function"]
                    if f["permissionMask"] & DENY_BIT: deny = True
                    mask_or |= f["permissionMask"]
                    
                    # Logic: Tightest limit wins
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
                    {"clientId": cid, "userId": uid, "system": system, "functionCode": fn, 
                     "client_dimensions": dict(dims), "arrangements": dict(arrs)},
                    {"$set": effective}, upsert=True
                )
                db.trace.insert_one({
                    "clientId": cid, "userId": uid, "system": system, "functionCode": fn, 
                    "client_dimensions": dict(dims), "arrangements": dict(arrs), 
                    "entries": trace_entries, "generatedAt": datetime.utcnow()
                })
    print("🚀 Materialization complete for all users and their respective systems.")

def inherit_role_step():
    if not check_state("cid", "defn"): return
    system = state["defn"]["system"]
    new_role_id = prompt("Enter ID for new inherited role: ")
    
    db.roles.update_one(
        {"_id": new_role_id}, 
        {"$set": {"clientId": state["cid"], "system": system}}, 
        upsert=True
    )
    
    existing_roles = list(db.roles.find({"clientId": state["cid"], "system": system, "_id": {"$ne": new_role_id}}))
    if not existing_roles:
        print("No other roles in this system to inherit from.")
        return

    for i, r in enumerate(existing_roles): print(f"[{i}] {r['_id']}")
    choices = prompt("Enter indices to inherit (e.g. 0,1): ").split(',')
    
    for idx in choices:
        try:
            parent_rid = existing_roles[int(idx.strip())]["_id"]
            parent_grants = list(db.role_entitlements.find({"roleId": parent_rid, "system": system}))
            for g in parent_grants:
                g.pop("_id", None)
                g["roleId"] = new_role_id
                db.role_entitlements.insert_one(g)
        except: continue
        
    print(f"✅ Role {new_role_id} created with inherited grants for system {system}.")

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
        ("Create/Select Client (Link System)", create_client_step),
        ("Create Dimensions Master Data", create_client_dimensions_master),
        ("Create Arrangements Master Data", create_arrangements_master),
        ("Create Users (Add to System Array)", create_users_step),
        ("Create Roles (System Specific)", create_roles_step),
        ("Assign User Roles", assign_user_roles_step),
        ("Generate Role Entitlements (from Master)", create_role_entitlements_step),
        ("Generate User Entitlements (from Master)", create_user_overrides_step),
        ("Materialize Effective Entitlements (Multi-System)", materialize_step),
        ("GENERATE ALL DATA (Batch)", generate_all_data),
        ("Inherit Role", inherit_role_step),
        ("Exit", sys.exit)
    ]

    while True:
        print("\n--- ENTITLEMENT MANAGEMENT MENU (Agnostic v3) ---")
        for i, (label, _) in enumerate(options):
            print(f"[{i:2}] {label}")
        
        curr_sys = state['defn']['system'] if state['defn'] else 'None'
        curr_cid = state['cid'] or 'None'
        print(f"\nContext: [System: {curr_sys}] [Client: {curr_cid}]")

        try:
            choice_str = prompt("Select action: ")
            choice = int(choice_str)
            if 0 <= choice < len(options):
                options[choice][1]()
            else:
                print("❌ Invalid choice.")
        except ValueError:
            print("❌ Please enter a number.")
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main_menu()