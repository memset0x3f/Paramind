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
        from .ClusterTypes import NodeProfile

        return NodeProfile
    if name == "NodeState":
        from .NodeInventory import NodeState

        return NodeState
    if name == "ClusterState":
        from .NodeInventory import ClusterState

        return ClusterState
    if name == "ShardAssignment":
        from .ClusterTypes import ShardAssignment

        return ShardAssignment
    if name == "PlacementPlan":
        from .ClusterTypes import PlacementPlan

        return PlacementPlan
    if name == "NodeReconfigurationAction":
        from .ClusterTypes import NodeReconfigurationAction

        return NodeReconfigurationAction
    if name == "ReconfigurationPlan":
        from .ClusterTypes import ReconfigurationPlan

        return ReconfigurationPlan
    if name == "build_node_profile":
        from .DeviceProfile import build_node_profile

        return build_node_profile
    if name == "snapshot_local_node_profile":
        from .DeviceProfile import snapshot_local_node_profile

        return snapshot_local_node_profile
    if name == "build_node_state":
        from .NodeInventory import build_node_state

        return build_node_state
    if name == "snapshot_local_node_state":
        from .DeviceProfile import snapshot_local_node_state

        return snapshot_local_node_state
    if name == "choose_coordinator":
        from .ClusterPlanner import choose_coordinator

        return choose_coordinator
    if name == "coordinator_score":
        from .ClusterPlanner import coordinator_score

        return coordinator_score
    if name == "estimate_reference_stage_width":
        from .ClusterPlanner import estimate_reference_stage_width

        return estimate_reference_stage_width
    if name == "estimate_stage_time_for_node":
        from .ClusterPlanner import estimate_stage_time_for_node

        return estimate_stage_time_for_node
    if name == "plan_static_distribution":
        from .ClusterPlanner import plan_static_distribution

        return plan_static_distribution
    if name == "score_assignment_move_cost":
        from .ClusterPlanner import score_assignment_move_cost

        return score_assignment_move_cost
    if name == "replan_distribution":
        from .ClusterPlanner import replan_distribution

        return replan_distribution
    if name == "diff_assignment_changes":
        from .ClusterPlanner import diff_assignment_changes

        return diff_assignment_changes
    if name == "ClusterCoordinator":
        from .ClusterCoordinator import ClusterCoordinator

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
