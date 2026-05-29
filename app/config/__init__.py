"""
Application configuration module.

Exports the Settings singleton for centralized access to environment variables
and application-wide configuration values.
"""

from app.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
