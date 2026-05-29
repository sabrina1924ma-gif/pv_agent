"""
工具实现层。

每个模块是一个独立的工具函数，用 @tool 装饰器注册到 TOOL_REGISTRY。
导入本模块即完成所有工具的注册——agent 节点通过 TOOL_REGISTRY 按名查找。

工具与 intent 的映射关系定义在 app.agent.nodes.tool_executor.INTENT_TOOL_MAP。
"""

from app.tools.impl.device_status import device_status
from app.tools.impl.power_curve import power_curve
from app.tools.impl.fault_detection import fault_detection
from app.tools.impl.life_assessment import life_assessment
from app.tools.impl.report_generator import report_generator
from app.tools.impl.multi_station_summary import multi_station_summary

__all__ = [
    "device_status",
    "power_curve",
    "fault_detection",
    "life_assessment",
    "report_generator",
    "multi_station_summary",
]
