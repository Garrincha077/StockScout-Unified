from stock_scout.setups.accumulation_base import AccumulationBaseDetector
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.crash_base_stage1 import CrashBaseStage1Detector
from stock_scout.setups.ema_stack_launch import EMAStackLaunchDetector
from stock_scout.setups.glb import GLBDetector
from stock_scout.setups.guppy import GuppyDetector
from stock_scout.setups.high_rs import HighRSDetector
from stock_scout.setups.long_base_launch import LongBaseLaunchDetector
from stock_scout.setups.ma_cluster_volume_breakout import MAClusterVolumeBreakoutDetector
from stock_scout.setups.minervini import MinerviniDetector
from stock_scout.setups.rwb_squeeze_thrust import RWBSqueezeThrustDetector
from stock_scout.setups.tight_breakout import TightBreakoutDetector
from stock_scout.setups.weinstein import WeinsteinDetector

__all__ = [
    "AccumulationBaseDetector",
    "CrashBaseStage1Detector",
    "EMAStackLaunchDetector",
    "GLBDetector",
    "GuppyDetector",
    "HighRSDetector",
    "LongBaseLaunchDetector",
    "MAClusterVolumeBreakoutDetector",
    "MinerviniDetector",
    "RWBSqueezeThrustDetector",
    "SetupDetector",
    "SetupResult",
    "TightBreakoutDetector",
    "WeinsteinDetector",
]
