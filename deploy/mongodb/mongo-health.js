// Authenticate as the database-only app user; no administrative privileges.
(() => {
  let session;
  try {
    const connection = new Mongo("mongodb://127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=2000&connectTimeoutMS=2000");
    const database = connection.getDB("databack");
    const authenticated = database.auth(process.env.MONGODB_USERNAME, process.env.MONGODB_PASSWORD);
    if (authenticated !== 1 && authenticated?.ok !== 1) throw new Error();
    const hello = connection.getDB("admin").runCommand({hello: 1});
    if (hello.ok !== 1 || hello.setName !== "databack" || !hello.isWritablePrimary
        || hello.logicalSessionTimeoutMinutes === undefined) throw new Error();
    session = connection.startSession();
    session.startTransaction({readConcern: {level: "snapshot"}, writeConcern: {w: "majority"}});
    const metadata = session.getDatabase("databack").getCollection("_meta");
    metadata.updateOne({_id: "bootstrap-health"}, {$inc: {revision: 1}}, {upsert: true});
    if (metadata.findOne({_id: "bootstrap-health"}) === null) throw new Error();
    session.commitTransaction();
    session.endSession();
    quit(0);
  } catch (_) {
    if (session) {
      try { session.abortTransaction(); session.endSession(); } catch (_) {}
    }
    print("MongoDB application transaction readiness failed.");
    quit(1);
  }
})();
