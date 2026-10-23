import test_recovery_features
import test_mongo_store
import test_snapshots


class MongoRecoveryContract(test_mongo_store.MongoFixture, test_recovery_features.RecoveryFeaturesTests):
    contract_module = test_recovery_features


class MongoSnapshotContract(test_mongo_store.MongoFixture, test_snapshots.SnapshotTests):
    contract_module = test_snapshots
