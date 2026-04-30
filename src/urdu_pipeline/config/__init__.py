"""Configuration package: settings, model roles, and pricing.

All model IDs and prices live here. Stage code MUST NOT hardcode model IDs or
per-token prices anywhere else in the pipeline.
"""

from urdu_pipeline.config.model_roles import ModelRoles, get_model_roles
from urdu_pipeline.config.pricing import PricingTable, get_pricing_table
from urdu_pipeline.config.settings import Settings, get_settings, reset_settings_cache

__all__ = [
    "ModelRoles",
    "PricingTable",
    "Settings",
    "get_model_roles",
    "get_pricing_table",
    "get_settings",
    "reset_settings_cache",
]
