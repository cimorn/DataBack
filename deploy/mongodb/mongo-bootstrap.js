// Runs after the FINAL --auth/--keyFile/--replSet daemon starts. No secrets in argv.
(() => {
  let phase = "configuration";
  const rootUser = process.env.MONGO_ROOT_USERNAME;
  const rootPassword = process.env.MONGO_ROOT_PASSWORD;
  const appUser = process.env.MONGODB_USERNAME;
  const appPassword = process.env.MONGODB_PASSWORD;
  function authSucceeded(result) { return result === 1 || result?.ok === 1; }
  function validPassword(value) {
    return typeof value === "string" && value.length >= 32 && value.length <= 1024
      && new Set(value).size >= 8 && !/[\x00-\x1f\x7f]/.test(value) && !value.startsWith("REPLACE_");
  }
  function connect() {
    return new Mongo("mongodb://127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=2000&connectTimeoutMS=2000");
  }
  function waitForPrimary(connection) {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      try {
        const hello = connection.getDB("admin").runCommand({hello: 1});
        if (hello.ok === 1 && hello.setName === "databack" && hello.isWritablePrimary) return;
      } catch (_) { /* Retry only; never print driver diagnostics. */ }
      sleep(1000);
    }
    throw new Error("primary timeout");
  }
  try {
    if (![rootUser, appUser].every(value => typeof value === "string" && /^[A-Za-z0-9_-]{3,64}$/.test(value))
        || rootUser === appUser || !validPassword(rootPassword) || !validPassword(appPassword)
        || rootPassword === appPassword) throw new Error("invalid credentials");
    phase = "daemon connection";
    let connection;
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      try {
        connection = connect();
        if (connection.getDB("admin").runCommand({ping: 1}).ok === 1) break;
      } catch (_) { connection = null; }
      sleep(1000);
    }
    if (!connection) throw new Error("connection timeout");
    const admin = connection.getDB("admin");
    // Existing datasets authenticate first. A fresh/partially bootstrapped
    // dataset can use localhost exception until its first user exists.
    let authenticated = false;
    try { authenticated = authSucceeded(admin.auth(rootUser, rootPassword)); } catch (_) {}
    phase = "replica-set initialization";
    const hello = admin.runCommand({hello: 1});
    if (!hello.setName) {
      const initiated = admin.runCommand({replSetInitiate: {
        _id: "databack", members: [{_id: 0, host: "databack-mongodb:27017"}],
      }});
      if (initiated.ok !== 1) throw new Error("replica-set initialization failed");
    } else if (hello.setName !== "databack") throw new Error("unexpected replica set");
    phase = "primary election";
    waitForPrimary(connection);
    phase = "administrator authentication";
    if (!authenticated) {
      admin.createUser({user: rootUser, pwd: rootPassword, roles: [{role: "root", db: "admin"}]});
      if (!authSucceeded(admin.auth(rootUser, rootPassword))) throw new Error("root authentication failed");
    }
    const config = admin.runCommand({replSetGetConfig: 1});
    if (config.ok !== 1 || config.config._id !== "databack" || config.config.members.length !== 1
        || config.config.members[0].host !== "databack-mongodb:27017") throw new Error("unexpected member configuration");
    phase = "application account provisioning";
    const database = connection.getDB("databack");
    const existing = database.getUser(appUser);
    if (existing === null) {
      database.createUser({user: appUser, pwd: appPassword, roles: [{role: "readWrite", db: "databack"}]});
    } else if (existing.roles.length !== 1 || existing.roles[0].role !== "readWrite"
               || existing.roles[0].db !== "databack") throw new Error("unexpected application roles");
    // Dedicated metadata only; no ledger import or schema/data seeding.
    if (!database.getCollectionNames().includes("_meta")) database.createCollection("_meta");
    phase = "application authentication";
    const app = connect().getDB("databack");
    if (!authSucceeded(app.auth(appUser, appPassword))) throw new Error("application authentication failed");
    quit(0);
  } catch (_) {
    print("MongoDB bootstrap failed during " + phase + "; check private configuration and dedicated data permissions.");
    quit(1);
  }
})();
