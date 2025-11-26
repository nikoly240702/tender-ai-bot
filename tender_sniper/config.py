"""
Feature Configuration Loader for Tender Sniper

This module provides utilities to check feature flags and configure
components based on config/features.yaml settings.
"""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class FeatureConfig:
    """Feature configuration manager."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize feature config loader.

        Args:
            config_path: Path to features.yaml, defaults to config/features.yaml
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / 'config' / 'features.yaml'

        self.config_path = config_path
        self._config = None
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            self._config = {}
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Warning: Failed to load features config: {e}")
            self._config = {}

    def reload(self) -> None:
        """Reload configuration from file."""
        self._load_config()

    @property
    def is_tender_sniper_enabled(self) -> bool:
        """Check if Tender Sniper is enabled."""
        return self._config.get('tender_sniper', {}).get('enabled', False)

    def is_component_enabled(self, component: str) -> bool:
        """Check if specific Tender Sniper component is enabled.

        Args:
            component: Component name (e.g., 'realtime_parser', 'smart_matching')

        Returns:
            True if component is enabled, False otherwise
        """
        if not self.is_tender_sniper_enabled:
            return False

        return self._config.get('tender_sniper', {}).get('components', {}).get(component, False)

    def is_feature_enabled(self, feature: str) -> bool:
        """Check if general feature is enabled.

        Args:
            feature: Feature name from features section

        Returns:
            True if feature is enabled, False otherwise
        """
        return self._config.get('features', {}).get(feature, False)

    def get_limit(self, limit_name: str) -> Optional[Any]:
        """Get limit value.

        Args:
            limit_name: Limit name (e.g., 'max_file_size', 'cache_ttl_days')

        Returns:
            Limit value or None if not found
        """
        return self._config.get('limits', {}).get(limit_name)

    def is_mode_active(self, mode: str) -> bool:
        """Check if specific mode is active.

        Args:
            mode: Mode name (e.g., 'development', 'production')

        Returns:
            True if mode is active, False otherwise
        """
        return self._config.get('modes', {}).get(mode, False)

    def is_experimental_enabled(self, feature: str) -> bool:
        """Check if experimental feature is enabled.

        Args:
            feature: Experimental feature name

        Returns:
            True if experimental feature is enabled, False otherwise
        """
        return self._config.get('experimental', {}).get(feature, False)

    def get_all_config(self) -> Dict[str, Any]:
        """Get full configuration dictionary.

        Returns:
            Complete configuration dictionary
        """
        return self._config.copy()


# Global instance
feature_config = FeatureConfig()


# Convenience functions
def is_tender_sniper_enabled() -> bool:
    """Check if Tender Sniper is enabled."""
    return feature_config.is_tender_sniper_enabled


def is_component_enabled(component: str) -> bool:
    """Check if specific Tender Sniper component is enabled."""
    return feature_config.is_component_enabled(component)


def is_feature_enabled(feature: str) -> bool:
    """Check if general feature is enabled."""
    return feature_config.is_feature_enabled(feature)


def get_limit(limit_name: str) -> Optional[Any]:
    """Get limit value."""
    return feature_config.get_limit(limit_name)


def is_development_mode() -> bool:
    """Check if development mode is active."""
    return feature_config.is_mode_active('development')


def is_production_mode() -> bool:
    """Check if production mode is active."""
    return feature_config.is_mode_active('production')