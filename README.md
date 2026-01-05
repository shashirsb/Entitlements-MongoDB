
---

# Entitlement Engine — Event-Driven Effective Permission Compiler (v02)

This project implements an **enterprise-scale entitlement model** with:

* strict tenant isolation
* user + group inheritance
* deny-override semantics
* event-driven recomputation
* explainable, materialized **effective entitlements**

It supports **high-fan-out users**, heavy entitlement workloads, and real-time permission propagation.

---

## 🧩 Conceptual Model

Entitlements answer four questions:

| Question                      | Field                       |
| ----------------------------- | --------------------------- |
| **Who** is acting?            | `userId`                    |
| **What actions** are allowed? | `permissionMask`            |
| **Where** do they apply?      | `arrangementId / accountId` |
| **Under which product?**      | `productCode`               |

Permissions may be assigned:

* **directly to users**
* **indirectly via groups**

All data is tenant-scoped by `clientId`.

---

## 🗂 Schema Overview

### **users** — Who can act

```json
{
  "_id": "...",
  "clientId": "CLIENT_X",
  "accessId": "user_123",
  "status": "ACTIVE"
}
```

---

### **user_groups** — Logical role containers

```json
{
  "_id": "...",
  "clientId": "CLIENT_X",
  "name": "ANALYST_OPS"
}
```

---

### **user_group_membership** — User → Group mapping

```json
{
  "clientId": "CLIENT_X",
  "userId": "...",
  "groupId": "..."
}
```

A user may belong to **many groups**.

---

### **products** — Capability definitions

```json
{
  "clientId": "CLIENT_X",
  "productCode": "TRADE",
  "functions": {
    "IMPM": 1,
    "FFCCX": 2,
    "FFAAPX": 4,
    "DENY": 8
  }
}
```

Bit flags are used for compact permission masks.

---

### **user_product_arrangement** — Direct user entitlements

```json
{
  "clientId": "CLIENT_X",
  "userId": "...",
  "accountId": "ACC_001",
  "productCode": "TRADE",
  "permissionMask": 3
}
```

Meaning:

> This user may perform actions X,Y on this arrangement.

---

### **group_product_arrangement** — Group-inherited entitlements

```json
{
  "clientId": "CLIENT_X",
  "groupId": "...",
  "accountId": "ACC_001",
  "productCode": "TRADE",
  "permissionMask": 4
}
```

Users inherit permissions from any groups they belong to.

---

## 🚨 Deny Precedence

If any assignment includes the **DENY bit**:

```
DENY overrides all other permissions
```

This ensures deterministic conflict resolution.

---

## 🧮 Effective Permissions (Materialized Output)

All merged permissions are written to:

### **effective_entitlements**

```json
{
  "clientId": "CLIENT_X",
  "userId": "...",
  "arrangementId": "ACC_001",
  "productCode": "TRADE",
  "effectiveMask": 8,
  "deny": true,
  "trace": [
    { "source": "USER", "mask": 3 },
    { "source": "GROUP:5f2a...", "mask": 8 }
  ],
  "lastUpdated": "2025-01-01T12:00:00Z"
}
```

### ✔ One row per

`(userId, arrangementId, productCode)`

### ✔ Includes **trace history** for explainability

Auditors can see:

* where the permission came from
* whether deny originated from a user or group
* why a permission exists

---

## ⚙️ Real-Time Recompute (Change-Stream Compiler)

The compiler listens for writes to:

* `user_product_arrangement`
* `group_product_arrangement`
* `user_group_membership`

When a change occurs:

1. Determine **which users are affected**
2. Recompute only those users
3. Update `effective_entitlements`

This avoids:

* full-table recomputes
* stale security state
* unnecessary compute load

The system is:

> **incremental, event-driven, and low-latency**

---

## 🎛 Delta Operations (Test Harness Options a–j)

The delta runner simulates real IAM change events.

| Code  | Action                      | Who is recomputed            |
| ----- | --------------------------- | ---------------------------- |
| **a** | Add user to group           | that user                    |
| **b** | Remove user from group      | that user                    |
| **c** | Move user between groups    | that user                    |
| **d** | Add entitlement to group    | all users in group           |
| **e** | Modify group entitlement    | all users in group           |
| **f** | Remove group entitlement    | all users in group           |
| **g** | Add direct user entitlement | that user                    |
| **h** | Remove user entitlement     | that user                    |
| **i** | Modify user entitlement     | that user                    |
| **j** | Execute batch deltas        | all impacted users (deduped) |

These validate:

* inheritance rules
* deny-precedence behavior
* merge correctness
* recompute performance

---

## 🧠 Permission Merge Algorithm

For each `(arrangementId, productCode)`:

1. Collect masks from:

   * direct user entitlements
   * group entitlements via membership
2. OR-combine all bits
3. If any mask contains **DENY → effectiveMask = DENY**
4. Record all contributing sources in `trace[]`

---

## 🏎 Why This Architecture Scales

* tenant-scoped collections (shard-safe)
* event-driven recompute (no full scans)
* user-level materialization (O(1) reads)
* compact bitmask permissions
* explainable trace model
* deterministic deny-override rules

Supports environments with:

* millions of entitlements
* heavy-privilege users
* frequent entitlement changes

---

## 📦 Components

| File                            | Purpose                                       |
| ------------------------------- | --------------------------------------------- |
| `generateData.py`               | Loads sample tenant / user / entitlement data |
| `entitlement_compiler_v02.py`   | Real-time change-stream recompute engine      |
| `deltaEntitlement.py` | Interactive delta test harness                |

---

## 🧪 Running the System

Generate Data:

```bash
python generateData.py
```

Start the compiler:

```bash
python entitlement_compiler_v02.py
```

Run delta test harness:

```bash
python deltaEntitlement.py
```

Select options **a–j** to simulate entitlement changes.

---

## 📝 License / Contributions

PRs welcome — especially around:

* optimization strategies
* trace analytics
* batching behavior
* heavy-user scenarios

---

## 📣 Questions / Enhancements

Open a discussion or ask for:

* architecture diagrams
* performance tuning profiles
* audit-report export format
* Kubernetes deployment guide

---

If you want, I can also generate:

* a **diagram version** of this README, or
* a **diagram + PPT slide pack** for stakeholder briefings.
