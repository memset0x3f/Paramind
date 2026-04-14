# Lightweight exports available eagerly; heavy torch/transformers users stay lazy.
from .ModelSplitter import QwenSlice
from .ShardConfig import ModelFamily, ShardConfig


def __getattr__(name):
    if name == "ShardLoader":
        from .ShardLoader import ShardLoader

        return ShardLoader
    if name == "ModelShard":
        from .ShardLoader import ModelShard

        return ModelShard
    if name == "ModelRegistry":
        from .ModelRegistry import ModelRegistry

        return ModelRegistry
    if name == "LocalInferenceEngine":
        from .InferenceEngine import LocalInferenceEngine

        return LocalInferenceEngine
    if name == "DistributedInferenceEngine":
        from .DistributedEngine import DistributedInferenceEngine

        return DistributedInferenceEngine
    if name == "NodeProfile":
        from scheduler.ClusterTypes import NodeProfile

        return NodeProfile
    if name == "NodeState":
        from scheduler.NodeInventory import NodeState

        return NodeState
    if name == "ClusterState":
        from scheduler.NodeInventory import ClusterState

        return ClusterState
    if name == "ShardAssignment":
        from scheduler.ClusterTypes import ShardAssignment

        return ShardAssignment
    if name == "PlacementPlan":
        from scheduler.ClusterTypes import PlacementPlan

        return PlacementPlan
    if name == "NodeReconfigurationAction":
        from scheduler.ClusterTypes import NodeReconfigurationAction

        return NodeReconfigurationAction
    if name == "ReconfigurationPlan":
        from scheduler.ClusterTypes import ReconfigurationPlan

        return ReconfigurationPlan
    if name == "build_node_profile":
        from scheduler.DeviceProfile import build_node_profile

        return build_node_profile
    if name == "snapshot_local_node_profile":
        from scheduler.DeviceProfile import snapshot_local_node_profile

        return snapshot_local_node_profile
    if name == "build_node_state":
        from scheduler.NodeInventory import build_node_state

        return build_node_state
    if name == "snapshot_local_node_state":
        from scheduler.DeviceProfile import snapshot_local_node_state

        return snapshot_local_node_state
    if name == "plan_static_distribution":
        from scheduler.ClusterPlanner import plan_static_distribution

        return plan_static_distribution
    if name == "replan_distribution":
        from scheduler.ClusterPlanner import replan_distribution

        return replan_distribution
    if name == "diff_assignment_changes":
        from scheduler.ClusterPlanner import diff_assignment_changes

        return diff_assignment_changes
    if name == "ClusterCoordinator":
        from scheduler.ClusterCoordinator import ClusterCoordinator

        return ClusterCoordinator
    if name == "NodeRuntime":
        from .NodeRuntime import NodeRuntime

        return NodeRuntime
    if name == "ShardRecord":
        from .ShardRegistry import ShardRecord

        return ShardRecord
    if name == "ShardRegistry":
        from .ShardRegistry import ShardRegistry

        return ShardRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
