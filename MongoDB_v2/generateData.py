import hashlib
import random
from datetime import datetime
from pymongo import MongoClient, InsertOne
from bson import ObjectId

# =====================================================
# CONFIG — tune workload realism here
# =====================================================
MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "entitlements_v2"

TENANTS = 1                     # number of clients
LIGHT_USERS_PER_CLIENT = 2    # 10–50 entitlements
MEDIUM_USERS_PER_CLIENT = 0     # 1k–5k entitlements
HEAVY_USERS_PER_CLIENT = 0      # 25k–100k entitlements

GROUPS_PER_CLIENT = 1
PRODUCTS_PER_CLIENT = 2

USER_GROUP_DENSITY = 0.7        # probability user belongs to a group
GROUP_OVERLAP_FACTOR = 0.5      # (kept for entitlement overlap, not membership dupes)
DENY_RATE = 0.15                # % of entitlements that are DENY

BATCH_SIZE = 1000

# Bitmask model
DENY_BIT = 0b1000
ALLOW_USER = 0b0011
ALLOW_GROUP = 0b0100

PRODUCT_CODES = ["MERCH", "PAY", "FX"]

client = MongoClient(MONGO_URI)
db = client[DB_NAME]


# =====================================================
# UTIL — deterministic ObjectIds (repeatable dataset)
# =====================================================
def oid(value: str) -> ObjectId:
    return ObjectId(hashlib.md5(value.encode()).hexdigest()[:24])


# =====================================================
# CLEAN RESET
# =====================================================
def cleanup():
    print("🧹 Resetting dataset...")
    for col in [
        "clients",
        "users",
        "user_groups",
        "products",
        "user_group_membership",
        "user_product_arrangement",
        "group_product_arrangement",
        "effective_entitlements",
    ]:
        db[col].delete_many({})


# =====================================================
# INDEXES
# =====================================================
def setup_indexes():
    print("⚙️ Creating indexes...")

    db.users.create_index([("clientId", 1), ("accessId", 1)], unique=True)
    db.user_groups.create_index([("clientId", 1), ("name", 1)], unique=True)

    db.products.create_index([("clientId", 1), ("productCode", 1)], unique=True)

    db.user_group_membership.create_index(
        [("clientId", 1), ("userId", 1), ("groupId", 1)], unique=True
    )

    db.user_product_arrangement.create_index(
        [("clientId", 1), ("userId", 1), ("accountId", 1), ("productCode", 1)],
        unique=True,
    )

    db.group_product_arrangement.create_index(
        [("clientId", 1), ("groupId", 1), ("accountId", 1), ("productCode", 1)],
        unique=True,
    )

    db.effective_entitlements.create_index(
        [
            ("clientId", 1),
            ("userId", 1),
            ("arrangementId", 1),
            ("productCode", 1),
        ],
        unique=True,
    )


# =====================================================
# PRODUCT CATALOG
# =====================================================
def create_products(client_id):
    products = []
    for p in range(PRODUCTS_PER_CLIENT):
        code = PRODUCT_CODES[p % len(PRODUCT_CODES)]
        products.append(
            {
                "_id": oid(f"{client_id}_PROD_{code}"),
                "clientId": client_id,
                "productCode": code,
                "functions": {
                    "IMPM": 1,
                    "FFCCX": 2,
                    "FFAAPX": 4,
                    "DENY": DENY_BIT,
                },
            }
        )
    db.products.insert_many(products)
    return [p["productCode"] for p in products]


# =====================================================
# GROUPS
# =====================================================
def create_groups(client_id):
    groups = []
    for g in range(GROUPS_PER_CLIENT):
        groups.append(
            {
                "_id": oid(f"{client_id}_GROUP_{g}"),
                "clientId": client_id,
                "name": f"{client_id}_GROUP_{g}",
            }
        )
    db.user_groups.insert_many(groups)
    return groups


# =====================================================
# USERS (light / medium / heavy tiers)
# =====================================================
def create_users(client_id):
    users = []

    def mk_user(i, tier):
        return {
            "_id": oid(f"{client_id}_USER_{tier}_{i}"),
            "clientId": client_id,
            "accessId": f"{tier}_user_{i}",
            "tier": tier,
            "status": "ACTIVE",
        }

    users += [mk_user(i, "LIGHT") for i in range(LIGHT_USERS_PER_CLIENT)]
    users += [mk_user(i, "MEDIUM") for i in range(MEDIUM_USERS_PER_CLIENT)]
    users += [mk_user(i, "HEAVY") for i in range(HEAVY_USERS_PER_CLIENT)]

    db.users.insert_many(users)
    return users


# =====================================================
# ENTITLEMENTS
# =====================================================
def generate_entitlements(client_id, users, groups, product_codes):
    mem_ops, user_arr_ops, grp_arr_ops = [], [], []

    for u in users:
        # ---------------- Memberships (deduped correctly) ----------------
        assigned_groups = set()

        for g in groups:
            if random.random() <= USER_GROUP_DENSITY:
                key = (u["_id"], g["_id"])
                if key in assigned_groups:
                    continue
                assigned_groups.add(key)

                mem_ops.append(
                    InsertOne(
                        {
                            "clientId": client_id,
                            "userId": u["_id"],
                            "groupId": g["_id"],
                        }
                    )
                )

        # ---------------- Entitlement volume by tier ----------------
        if u["tier"] == "LIGHT":
            count = random.randint(10, 50)
        elif u["tier"] == "MEDIUM":
            count = random.randint(1000, 5000)
        else:
            count = random.randint(25000, 100000)

        for i in range(count):
            product = random.choice(product_codes)
            acc = f"ACC_{hash(u['_id']) % 9999}_{i}"

            mask = ALLOW_USER
            if random.random() < DENY_RATE:
                mask = DENY_BIT

            user_arr_ops.append(
                InsertOne(
                    {
                        "clientId": client_id,
                        "userId": u["_id"],
                        "accountId": acc,
                        "productCode": product,
                        "permissionMask": mask,
                    }
                )
            )

        if len(user_arr_ops) >= BATCH_SIZE:
            db.user_product_arrangement.bulk_write(user_arr_ops)
            user_arr_ops.clear()

    # ---------------- Group-level entitlements (conflict + overlap cases) ----------------
    for g in groups:
        for i in range(50):
            product = random.choice(product_codes)
            acc = f"ACC_SHARED_{i}"

            mask = ALLOW_GROUP if random.random() > DENY_RATE else DENY_BIT

            grp_arr_ops.append(
                InsertOne(
                    {
                        "clientId": client_id,
                        "groupId": g["_id"],
                        "accountId": acc,
                        "productCode": product,
                        "permissionMask": mask,
                    }
                )
            )

        if len(grp_arr_ops) >= BATCH_SIZE:
            db.group_product_arrangement.bulk_write(grp_arr_ops)
            grp_arr_ops.clear()

    # ---------------- Final flush ----------------
    if mem_ops:
        db.user_group_membership.bulk_write(mem_ops)
    if user_arr_ops:
        db.user_product_arrangement.bulk_write(user_arr_ops)
    if grp_arr_ops:
        db.group_product_arrangement.bulk_write(grp_arr_ops)


# =====================================================
# MAIN WORKFLOW
# =====================================================
def generate():
    cleanup()
    setup_indexes()

    for t in range(TENANTS):
        client_id = f"CLIENT_{t}"
        print(f"\n🏗  Generating data for {client_id}")

        db.clients.insert_one(
            {"clientId": client_id, "createdAt": datetime.utcnow()}
        )

        product_codes = create_products(client_id)
        groups = create_groups(client_id)
        users = create_users(client_id)

        generate_entitlements(client_id, users, groups, product_codes)

        print(f"✓ Completed {client_id}")

    print("\n✅ v03 dataset ready — optimized for entitlement compiler testing.")


if __name__ == "__main__":
    generate()
