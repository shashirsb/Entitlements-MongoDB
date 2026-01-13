import hashlib
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# =====================================================
# CONFIG
# =====================================================
MONGO_URI = "mongodb+srv://main_user:main_user1@demo.kssen.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "entitlement_v3_agnostic"
COLLECTION = "dimension_definitions"

# =====================================================
# HELPERS
# =====================================================
def compute_dim_key(system: str, client_dimensions: list) -> str:
    """
    dimKey = hash(system + sorted dimension names)
    Mapping fields (arrangements) are NOT part of identity
    """
    raw = system + "|" + "|".join(sorted(client_dimensions))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def prompt(msg: str) -> str:
    while True:
        v = input(msg).strip()
        if v:
            return v
        print("❌ Value cannot be empty")


# =====================================================
# MAIN
# =====================================================
def main():
    print("\n=== Dimension Hierarchy Definition Generator ===\n")

    try:
        # ---- Connect ----
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        print("✅ Connected to MongoDB\n")

        col = client[DB_NAME][COLLECTION]

        # ---- System ----
        system = prompt("System name (e.g. SystemA): ")

        # ---- Dimensions (identity) ----
        print("\nDefine DIMENSIONS (identity)")
        print("Examples: function, product, channel, feature")
        print("Type 'done' when finished\n")

        client_dimensions = []
        while True:
            d = input("Dimension name: ").strip().lower()
            if d == "done":
                break

            if not d:
                print("❌ Dimension name cannot be empty")
                continue

            if d in client_dimensions:
                print("❌ Dimension already added")
                continue

            client_dimensions.append(d)

        if not client_dimensions:
            print("\n❌ At least one dimension is required")
            return

        # ---- Dimension Map (scope) ----
        print("\nDefine DIMENSION MAP (scope, not identity)")
        print("Examples: account, portfolio, region")
        print("Press 'done' immediately if no mapping is required\n")

        dimension_map = []
        while True:
            m = input("Arragement name: ").strip().lower()
            if m == "done":
                break

            if not m:
                print("❌ Arragement name cannot be empty")
                continue

            if m in dimension_map:
                print("❌ Mapping already added")
                continue

            dimension_map.append(m)

        # ---- Build ----
        dim_key = compute_dim_key(system, client_dimensions)

        doc = {
            "system": system,
            "client_dimensions": client_dimensions,
            "arrangements": dimension_map,
            "dimKey": dim_key
        }

        # ---- Upsert ----
        col.update_one(
            {"system": system, "dimKey": dim_key},
            {"$set": doc},
            upsert=True
        )

        # ---- Result ----
        print("\n✅ Dimension hierarchy saved (UPSERT)")
        print(f"System        : {system}")
        print(f"Dimensions    : {client_dimensions}")
        print(f"Dimension Map : {dimension_map}")
        print(f"dimKey        : {dim_key}")

    except PyMongoError as e:
        print("\n❌ MongoDB ERROR")
        print(e)


# =====================================================
# ENTRY POINT
# =====================================================
if __name__ == "__main__":
    main()
