// Closed canonical workload validator, preserved from the independently tested legacy view.
const kinds = [
  "router-request",
  "controller-operation",
  "benchmark-job",
  "media-job",
  "recipe-serve",
];
const ownerKinds = {
  router: kinds[0],
  controller: kinds[1],
  benchmark: kinds[2],
  media: kinds[3],
  recipe: kinds[4],
  manifest: kinds[4],
};
const states = [
  "checking",
  "admitted",
  "dispatched",
  "streaming",
  "queued",
  "running",
  "terminal",
  "configured",
  "absent",
  "unavailable",
  "unsupported",
];
const ownerAuthorities = {
  router: "router-memory",
  controller: "controller-store",
  benchmark: "benchmark-store",
  media: "media-store",
  recipe: "managed-status",
  manifest: "managed-status",
};
const ownerStates = {
  router: [
    "checking",
    "admitted",
    "dispatched",
    "streaming",
    "terminal",
    "unsupported",
  ],
  controller: ["running", "terminal", "unsupported"],
  benchmark: ["queued", "running", "terminal", "unsupported"],
  media: ["queued", "running", "terminal", "unsupported"],
  recipe: ["configured", "running", "absent", "unavailable", "unsupported"],
  manifest: ["configured", "running", "absent", "unavailable", "unsupported"],
};
const managedQualities = {
  configured: ["configured", "stale"],
  running: ["observed-running", "healthy-identity", "stale"],
  absent: ["absent", "stale"],
  unavailable: ["inspection-error"],
  unsupported: ["inspection-error"],
};
const SKEW_MICROSECONDS = 30000000n;
const sourceErrors = {
  "invalid-workload": "Invalid workload evidence",
  "unsupported-workload": "Unsupported workload evidence",
  "workload-source-unavailable": "Workload source unavailable",
  "future-workload-timestamp": "Future workload timestamp",
};
const statuses = ["complete", "partial", "unavailable"];
const active = new Set(states.slice(0, 6));
const MAX_BYTES = 8 * 1024 * 1024;

function require(value) {
  if (!value) throw new Error("Invalid workload response");
}
// Match the entire value: JavaScript's $ alone also accepts a final newline.
function hostId(value) {
  return (
    typeof value === "string" &&
    /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.exec(value)?.[0] === value
  );
}
function count(value, max = 1000000000) {
  return Number.isSafeInteger(value) && value >= 0 && value <= max;
}
function fields(value, required, optional = []) {
  require(value !== null && typeof value === "object" && !Array.isArray(value));
  require(required.every((key) => Object.hasOwn(value, key)));
  require(
    Object.keys(value).every(
      (key) => required.includes(key) || optional.includes(key),
    ),
  );
}
function timestamp(value) {
  require(
    typeof value === "string" &&
      /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.exec(value)?.[0] === value,
  );
  const milliseconds = Date.parse(value);
  require(Number(value.slice(0, 4)) > 0 && Number.isFinite(milliseconds));
  require(new Date(milliseconds).toISOString() === value.slice(0, 23) + "Z");
  return BigInt(milliseconds) * 1000n + BigInt(value.slice(23, 26));
}
function withinSkew(value, collectionTimes) {
  require(
    collectionTimes.every(
      (collected) => value - collected <= SKEW_MICROSECONDS,
    ),
  );
}
function validateSemantics(record) {
  // Mirror WorkloadRecord's closed relations, not independent enum membership.
  require(ownerStates[record.owner].includes(record.state));
  require(record.source_authority === ownerAuthorities[record.owner]);
  require(
    record.label ===
      record.kind
        .split("-")
        .map((word) => word[0].toUpperCase() + word.slice(1))
        .join(" "),
  );
  const managed = record.owner === "recipe" || record.owner === "manifest";
  require(
    managed
      ? managedQualities[record.state].includes(record.observation_quality)
      : record.observation_quality === "recorded",
  );
  const hasOutcome = Object.hasOwn(record, "outcome");
  if (record.state === "terminal") {
    const phases = {
      success: "completed",
      error: "failed",
      cancelled: "cancelled",
      timeout: "failed",
      rejected: "failed",
      disconnected: "failed",
    };
    require(
      hasOutcome &&
        Object.hasOwn(phases, record.outcome) &&
        record.phase === phases[record.outcome],
    );
    if (record.owner !== "router")
      require(
        [
          "success",
          "error",
          ...(record.owner === "controller" ? [] : ["cancelled"]),
        ].includes(record.outcome),
      );
  } else if (record.state === "unavailable" || record.state === "unsupported") {
    require(
      record.phase === record.state &&
        record.outcome ===
          (record.state === "unavailable" ? "unavailable" : "unknown"),
    );
  } else {
    require(!hasOutcome);
    const mediaPhase =
      record.owner === "media" &&
      ((record.state === "queued" && record.phase === "awaiting-approval") ||
        (record.state === "running" &&
          ["preparing", "submitting"].includes(record.phase)));
    require(record.phase === record.state || mediaPhase);
  }
}
function truncation(value, returned) {
  fields(value, ["returned", "omitted"]);
  require(count(value.returned, 1000) && value.returned === returned);
  require(value.omitted === null || count(value.omitted));
}
function combined(values) {
  if (values.length && values.every((value) => value === "unavailable"))
    return "unavailable";
  return values.some((value) => value !== "complete") ? "partial" : "complete";
}
function validateRecord(record, owner, host, collectionTimes) {
  fields(
    record,
    [
      "schema",
      "id",
      "kind",
      "owner",
      "host",
      "label",
      "state",
      "phase",
      "created_at",
      "updated_at",
      "source_timestamp",
      "source_authority",
      "observation_quality",
    ],
    ["outcome", "progress"],
  );
  require(
    record.schema === "anvil-workloads/v1" &&
      typeof record.id === "string" &&
      /^[0-9a-f]{64}$/.exec(record.id)?.[0] === record.id,
  );
  require(
    record.owner === owner &&
      record.host === host &&
      record.kind === ownerKinds[owner],
  );
  validateSemantics(record);
  const [created, updated, observed] = [
    "created_at",
    "updated_at",
    "source_timestamp",
  ].map((key) => timestamp(record[key]));
  require(created <= updated);
  const managed = owner === "recipe" || owner === "manifest";
  require(updated - observed <= (managed ? SKEW_MICROSECONDS : 0n));
  for (const value of [created, updated, observed])
    withinSkew(value, collectionTimes);
  if (Object.hasOwn(record, "progress") && record.progress !== null) {
    const progress = record.progress;
    fields(progress, ["completed", "total", "unit"]);
    require(
      count(progress.completed) &&
        (progress.total === null ||
          (count(progress.total) && progress.completed <= progress.total)),
    );
    require(["items", "requests", "steps"].includes(progress.unit));
  }
}
function validateSnapshot(body) {
  fields(body, ["ok", "data"]);
  require(body.ok === true);
  const fleet = body.data;
  fields(fleet, [
    "schema",
    "status",
    "collection_timestamp",
    "nodes",
    "truncation",
  ]);
  require(
    fleet.schema === "anvil-workloads/v1" && statuses.includes(fleet.status),
  );
  const fleetTime = timestamp(fleet.collection_timestamp);
  require(Array.isArray(fleet.nodes) && fleet.nodes.length <= 1000);
  let total = 0;
  const hosts = new Set();
  for (const node of fleet.nodes) {
    fields(node, [
      "schema",
      "host",
      "status",
      "collection_timestamp",
      "sources",
    ]);
    require(
      node.schema === fleet.schema &&
        hostId(node.host) &&
        !hosts.has(node.host) &&
        statuses.includes(node.status),
    );
    hosts.add(node.host);
    const nodeTime = timestamp(node.collection_timestamp);
    withinSkew(nodeTime, [fleetTime]);
    require(
      Array.isArray(node.sources) &&
        node.sources.length > 0 &&
        node.sources.length <= 6,
    );
    const owners = new Set();
    for (const source of node.sources) {
      fields(source, [
        "schema",
        "owner",
        "status",
        "collection_timestamp",
        "records",
        "truncation",
        "error",
      ]);
      require(
        source.schema === fleet.schema &&
          Object.hasOwn(ownerKinds, source.owner) &&
          !owners.has(source.owner),
      );
      owners.add(source.owner);
      require(
        statuses.includes(source.status) &&
          (source.error === null || Object.hasOwn(sourceErrors, source.error)),
      );
      const sourceTime = timestamp(source.collection_timestamp);
      withinSkew(sourceTime, [nodeTime, fleetTime]);
      require(Array.isArray(source.records) && source.records.length <= 200);
      total += source.records.length;
      require(total <= 1000);
      truncation(source.truncation, source.records.length);
      if (source.status === "complete")
        require(source.error === null && source.truncation.omitted === 0);
      if (source.status === "partial")
        require(source.error !== null || source.truncation.omitted !== 0);
      if (source.status === "unavailable")
        require(source.records.length === 0 && source.error !== null);
      const ids = new Set();
      for (const record of source.records) {
        validateRecord(record, source.owner, node.host, [
          sourceTime,
          nodeTime,
          fleetTime,
        ]);
        require(!ids.has(record.id));
        ids.add(record.id);
      }
    }
    require(
      node.status === combined(node.sources.map((source) => source.status)),
    );
  }
  truncation(fleet.truncation, total);
  const expected = combined(fleet.nodes.map((node) => node.status));
  require(
    fleet.status ===
      (expected === "complete" && fleet.truncation.omitted !== 0
        ? "partial"
        : expected),
  );
  return fleet;
}

export { validateSnapshot };
