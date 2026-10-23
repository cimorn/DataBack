import shlex
import shutil
import subprocess
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
NODE=Path(shutil.which("node") or "node")
class MongoBootstrapTests(unittest.TestCase):
    def test_daemon_logs_use_only_fully_redirected_console_without_logfile_rotation(self):
        shell = (ROOT / "../deploy/mongodb/mongo-entrypoint.sh").read_text(encoding="utf-8")
        # Parse the complete daemon invocation, including continued lines:
        # adding a Mongo log destination would bypass shell redirection and
        # reintroduce file-open/rotation work under the unprivileged NAS UID.
        invocation = next(line for line in shell.replace("\\\n", " ").splitlines()
                          if line.startswith("mongod "))
        argv = shlex.split(invocation)
        self.assertNotIn("--logpath", argv)
        self.assertNotIn("--syslog", argv)
        self.assertNotIn("--config", argv)
        self.assertEqual(argv[-4:], [">", "/dev/null", "2>&1", "&"])

    def test_bootstrap_fresh_then_restart_supports_documented_auth_return_value(self):
        path = ROOT / "../deploy/mongodb/mongo-bootstrap.js"
        self.assertTrue(path.is_file())
        # The ARM64 container boot is verified on the NAS. This shell-API double
        # checks bootstrap sequencing and restart behavior locally, including
        # MongoDB's documented numeric db.auth() success result.
        harness = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
let initialized = false, root = false, app = false, metadata = false, creates = 0, quitCode;
const events = [];
const env = {MONGO_ROOT_USERNAME: 'root_test', MONGODB_USERNAME: 'app_test',
 MONGO_ROOT_PASSWORD: 'RootTestPassword_' + '0123456789abcdef'.repeat(3),
 MONGODB_PASSWORD: 'AppTestPassword_' + 'fedcba9876543210'.repeat(3)};
function Mongo() {this.getDB = (database) => ({
 runCommand(command) {
  if (command.ping) return {ok: 1};
  if (command.hello) return initialized ? {ok: 1, setName: 'databack', isWritablePrimary: true} : {ok: 1};
  if (command.replSetInitiate) {events.push('init'); initialized = true; return {ok: 1};}
  if (command.replSetGetConfig) return {ok: 1, config: {_id: 'databack', members: [{host: 'databack-mongodb:27017'}]}};
  throw Error('unexpected command');
 },
 auth() {return database === 'admin' ? (root ? 1 : 0) : (app ? 1 : 0);},
 createUser(document) {
  assert(initialized); creates++;
  if (database === 'admin') {root = true; events.push('root');}
  else {assert(root); assert.deepEqual(document.roles, [{role: 'readWrite', db: 'databack'}]); app = true; events.push('app');}
 },
 getUser() {return app ? {roles: [{role: 'readWrite', db: 'databack'}]} : null;},
 getCollectionNames() {return metadata ? ['_meta'] : [];},
 createCollection(name) {assert.equal(name, '_meta'); metadata = true;}
});}
const code = fs.readFileSync(process.argv[1], 'utf8');
const context = {Mongo, process: {env}, Date, Set, sleep() {}, print() {}, quit(code) {quitCode = code;}};
vm.runInNewContext(code, context); assert.equal(quitCode, 0); assert.deepEqual(events, ['init','root','app']);
vm.runInNewContext(code, context); assert.equal(quitCode, 0); assert.equal(creates, 2);
console.log('fresh and restart bootstrap contract passed');
'''
        result = subprocess.run([str(NODE), "-e", harness, str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
