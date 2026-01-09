This `README.md` is designed to be GitHub-friendly, professional, and easy to follow. It provides a clear overview of the project structure, the entitlement logic, and a step-by-step guide on how to use the interactive management script.

---

# Multi-System Agnostic Entitlement Engine (V3)

A flexible, dimension-based **Role-Based Access Control (RBAC)** and **Attribute-Based Access Control (ABAC)** engine powered by MongoDB. This project provides a system-agnostic way to define, grant, and materialize complex entitlements into high-performance "Effective Entitlements."

## 🚀 Overview

In modern enterprise applications, calculating user permissions on the fly is often slow due to complex inheritance and dimension overrides. This engine solves that by **materializing** permissions:

1. **RBAC:** Users are assigned roles; roles have entitlements.
2. **ABAC/Overrides:** Specific users can have "Custom" overrides that bypass role logic.
3. **Dimensions:** Permissions aren't just "Yes/No"; they are bound to dimensions (e.g., `Region: US`, `Product: Gold`) and arrangements.
4. **Materialization:** The engine flattens these rules into an `effective_entitlements` collection for  lookups during API calls.

---

## 📂 Project Structure

| File | Description |
| --- | --- |
| `generateDim.py` | **Setup Script:** Generates system-specific dimension definitions (e.g., SystemA, SystemB). |
| `generateData.py` | **Interactive Tool:** The main management console to create clients, roles, users, and materialize data. |
| `requirements.txt` | List of Python dependencies (primarily `pymongo`). |

---

## 🛠 Prerequisites

* **Python 3.8+**
* **MongoDB Atlas** (or local instance)
* Dependencies installed via pip:
```bash
pip install pymongo

```



---

## 📖 Operational Guide

### 1. Define the System (Dimensions)

Before generating data, you must define what "Dimensions" your system uses. Run the dimension generator:

```bash
python generateDim.py

```

* This creates entries in the `dimension_definitions` collection.
* Example: A "Trading" system might have dimensions like `AssetClass` and `Desk`.

### 2. Run the Management Console

The core of the project is the interactive CLI. Run this to manage your data:

```bash
python generateData.py

```

---

## 🎮 Interactive Menu Options

Once you launch `generateData.py`, you will see the following menu:

| Option | Action | Description |
| --- | --- | --- |
| **[0]** | **Clear Data** | Truncates all collections (except definitions) and ensures MongoDB indexes. |
| **[1]** | **Select System** | Choose which system definition (e.g., SystemA) to work with. |
| **[2-4]** | **Core Entities** | Create `CLIENT_1`, standard Users (`USER_1`, `USER_2`), and basic Roles. |
| **[5]** | **Assign Roles** | Maps Users to Roles in the `user_roles` collection. |
| **[6]** | **Role Grants** | Generates random entitlement rules for created Roles. |
| **[7]** | **User Overrides** | Generates specific custom entitlements for users in `CUSTOM` mode. |
| **[8]** | **Materialize** | **The Engine:** Computes the "winning" permission for every user/function and saves to `effective_entitlements`. |
| **[9]** | **GENERATE ALL** | One-click execution of steps 0 through 8. |
| **[10]** | **Inherit Role** | Create a new role (e.g., `MANAGER`) by selecting and merging existing roles. |

---

## 🔍 Data Model & Logic

### Permission Logic

* **Allow/Deny:** We use a bitwise mask. `0b1000` (8) represents a **DENY**.
* **Deny Priority:** If a user inherits three "Allow" masks but one "Deny" mask for the same dimension, the effective result is **DENY**.
* **Limits:** For Roles, the engine takes the **minimum** numeric limit (most restrictive). For Custom overrides, the user-specific limit wins.

### Traceability

Every time you run **Materialization (Step 8)**, a record is created in the `trace` collection. This allows administrators to see exactly *why* a user was granted a certain permission (e.g., "Inherited from ROLE_1 and ROLE_2").

---

## 🛠 Configuration

To point the script to your database, update the `MONGO_URI` and `DB_NAME` at the top of the scripts:

```python
MONGO_URI = "mongodb+srv://<user>:<password>@cluster.mongodb.net/..."
DB_NAME = "entitlement_v3_agnostic"

```

---

**Would you like me to add a section on how to query the materialized data using a sample Python snippet?**



This **README.md** covers the entire project lifecycle, architecture, and the newly implemented Access Pattern Dashboard. It is designed to act as the primary documentation for the **Transactional Delta Entitlement Engine**.

---

# 🛡️ Transactional Delta Entitlement Engine (V14)

A high-performance, **minimal-touch** entitlement engine built on MongoDB. This system manages complex permission sets using bitwise logic, ensures data integrity via ACID transactions, and provides a "Delta-only" update mechanism to reduce database I/O.

## 🚀 Core Features

* **Transactional Integrity:** All mutations (assigning roles, adding grants) and their subsequent recomputations are wrapped in **MongoDB Client Sessions**.
* **Delta-Only Recompute:** The engine snapshots current state and only executes `UpdateOne` or `DeleteOne` operations if the permission mask or limits have actually changed.
* **Security Circuit Breaker:** Uses a dominant **DENY_BIT (Bit 1)**. If any assigned role contains a 1, all other permissions are ignored, and the user is blocked.
* **Multi-Dimension Support:** Entitlements are calculated based on a matrix of Function Codes, Dimensions (Product, Region), and Arrangements (Accounts).
* **Access Pattern Dashboard:** Pre-built queries for common administrative requirements (Client Discovery, User Materialization).

---

## 🏗️ Architecture & Schema

The system uses a "Materialized View" strategy. Permissions are defined in Roles but are pre-computed into an `effective_entitlements` collection for sub-millisecond runtime checks.

| Collection | Role |
| --- | --- |
| `users` | Stores user identity and operation mode (`ROLE` or `CUSTOM`). |
| `roles` | Groupings of permissions within a specific system. |
| `role_entitlements` | The "Source of Truth" linking Roles to Functions, Masks, and Limits. |
| `user_entitlements` | The "Source of Truth" linking Users to Functions, Masks, and Limits. |
| `effective_entitlements` | The **Materialized View** used by the application for access checks. |
| `trace` | Audit logs storing `before` and `after` snapshots of every permission change. |

---

## 🛠️ Access Patterns (Option `n`)

The engine provides 5 standardized access patterns to browse the entitlement landscape:

1. **Get Client Entitlements:** Fetches **all** materialized data for a specific `clientId`.
2. **Client Dimension Discovery:** Finds all arrangements (e.g., Accounts) available to a client for a specific dimension (e.g., Product).
3. **Client User List:** Lists all users associated with a specific client.
4. **User Materialized State:** Detailed dump of everything a specific user is allowed to do.
5. **Targeted User Arrangement:** Finds specific arrangements (Accounts) for a User + Product combo.

---

## 🚦 Bitwise Logic Decoder

The system defaults to a **DENY_BIT = 1**. Here is the standard bit-map used for permissions:

| Bit | Value | Permission |
| --- | --- | --- |
| **0** | **1** | **⛔ DENY (Circuit Breaker)** |
| **1** | **2** | Write |
| **2** | **4** | Approve |
| **3** | **8** | Execute |

**Example Calculation:**

* Role A (Write: 2) + Role B (Approve: 4) = **Mask 6** (Write + Approve).
* Role A (Write: 2) + Role C (Deny: 1) = **Mask 1** (Access Revoked).

---

## 💻 Installation & Setup

1. **Prerequisites:** Python 3.8+ and a MongoDB Cluster (Atlas or Local).
2. **Dependencies:** ```bash
pip install pymongo
```

```


3. **Configuration:** Update the `MONGO_URI` in `deltaEntitlement.py` with your credentials.
4. **Initialization:** Run the script and use **Option i** to inspect the system or **Option setup** to initialize discovery data.

---

## 🎮 CLI Menu Guide

| Option | Action |
| --- | --- |
| `a` | **Assign User:** Link a user to a role and trigger recompute. |
| `b` | **Remove User:** Unlink a role and surgically delete entitlements. |
| `g` | **Override:** Switch a user to `CUSTOM` mode for unique grants. |
| `i` | **Inspect:** View the "Role Perspective" and "User Perspective." |
| `k` | **Global Recompute:** Force-rebuild the materialized view for all users. |
| `m` | **Bitwise Demo:** Test how different masks merge and trigger Deny logic. |
| `n` | **Access Dashboard:** Run the 5 predefined Access Patterns. |

---

## 📜 Audit & Trace

Every time the engine detects a "Delta" (a change in state), it inserts a document into the `trace` collection:

```json
{
  "userId": "USER_0",
  "event": "CHANGE",
  "before": {"mask": 6, "limit": 1000},
  "after": {"mask": 1, "limit": 1000},
  "ts": "2026-01-09T..."
}

```

---

**Would you like me to generate a `docker-compose.yml` file to help you spin up a local MongoDB environment for testing this project?**