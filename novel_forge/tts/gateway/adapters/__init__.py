"""TTS provider adapters package."""

from novel_forge.tts.gateway.adapters.dashscope_adapter import DashScopeTTSAdapter
from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter
from novel_forge.tts.gateway.adapters.minimax_adapter import MiniMaxTTSAdapter
from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

__all__ = [
    "DashScopeTTSAdapter",
    "LocalTTSAdapter",
    "MiniMaxTTSAdapter",
    "MockTTSAdapter",
    "TencentTTSAdapter",
]
"""TTS provider adapters package."""
