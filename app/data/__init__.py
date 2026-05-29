"""Mock data layer — provides realistic sample data for development and testing."""

from app.data.mock import (
    STATIONS,
    get_station,
    get_power_data,
    get_fault_data,
    get_life_assessment,
)

__all__ = [
    "STATIONS",
    "get_station",
    "get_power_data",
    "get_fault_data",
    "get_life_assessment",
]
