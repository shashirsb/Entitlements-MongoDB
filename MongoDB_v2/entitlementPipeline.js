exports.buildPipeline = ({ clientId, userId }) => [
  // 1️⃣ Direct user entitlements
  { $match: { clientId, userId } },

  {
    $lookup: {
      from: "user_product_arrangement",
      localField: "userId",
      foreignField: "userId",
      as: "userArr"
    }
  },
  { $unwind: "$userArr" },

  {
    $project: {
      clientId: 1,
      userId: 1,
      arrangementId: "$userArr.accountId",
      productCode: "$userArr.productCode",
      mask: "$userArr.permissionMask",
      source: { $literal: "USER" }
    }
  },

  // 2️⃣ Union group-derived entitlements
  {
    $unionWith: {
      coll: "user_group_membership",
      pipeline: [
        { $match: { clientId, userId } },
        {
          $lookup: {
            from: "group_product_arrangement",
            let: { gid: "$groupId" },
            pipeline: [
              {
                $match: {
                  $expr: {
                    $and: [
                      { $eq: ["$clientId", clientId] },
                      { $eq: ["$groupId", "$$gid"] }
                    ]
                  }
                }
              }
            ],
            as: "grpArr"
          }
        },
        { $unwind: "$grpArr" },
        {
          $project: {
            clientId: 1,
            userId: 1,
            arrangementId: "$grpArr.accountId",
            productCode: "$grpArr.productCode",
            mask: "$grpArr.permissionMask",
            source: { $concat: ["GROUP:", { $toString: "$groupId" }] }
          }
        }
      ]
    }
  },

  // 3️⃣ Merge by entitlement key
  {
    $group: {
      _id: {
        clientId: "$clientId",
        userId: "$userId",
        arrangementId: "$arrangementId",
        productCode: "$productCode"
      },
      sources: { $push: { source: "$source", mask: "$mask" } },
      denyPresent: {
        $max: { $cond: [{ $eq: ["$mask", 8] }, 1, 0] }
      },
      mergedMask: { $bitOr: "$mask" }
    }
  },

  // 4️⃣ Precedence: DENY wins, else OR-merge
  {
    $project: {
      _id: 0,
      clientId: "$_id.clientId",
      userId: "$_id.userId",
      arrangementId: "$_id.arrangementId",
      productCode: "$_id.productCode",
      effectiveMask: {
        $cond: [{ $eq: ["$denyPresent", 1] }, 8, "$mergedMask"]
      },
      deny: { $eq: ["$denyPresent", 1] },
      trace: "$sources",
      lastUpdated: "$$NOW"
    }
  },

  // 5️⃣ Upsert into effective store
  {
    $merge: {
      into: "effective_entitlements",
      on: ["clientId", "userId", "arrangementId", "productCode"],
      whenMatched: "replace",
      whenNotMatched: "insert"
    }
  }
];
