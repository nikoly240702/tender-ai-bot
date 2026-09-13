"""Приёмочный тест конфигурации filters_v2.yaml (ТЗ, Этап 3.1):
конфиг должен грузиться и резолвиться без единой ошибки."""

import copy
from pathlib import Path

import pytest
import yaml

from tender_sniper.filters.config import load_config, ConfigError

CONFIG_PATH = Path(__file__).parent.parent.parent / 'filters_v2.yaml'


@pytest.mark.unit
def test_real_config_loads_without_errors():
    cfg = load_config(CONFIG_PATH)
    assert len(cfg.filters) == 50
    assert len(cfg.deprecated) == 13


@pytest.mark.unit
def test_real_config_has_unique_keywords():
    cfg = load_config(CONFIG_PATH)
    all_keywords = [kw for f in cfg.filters for kw in f.keywords]
    assert len(all_keywords) == len(set(all_keywords)) == 1266


@pytest.mark.unit
def test_wide_ural_include_directive_resolves():
    """@include:WIDE в WIDE_URAL разворачивается в 57 регионов (51 + 6 УФО)."""
    cfg = load_config(CONFIG_PATH)
    wide = next(f for f in cfg.filters if f.regions_preset == 'WIDE')
    assert len(wide.regions) == 51


@pytest.mark.unit
class TestConfigErrors:
    """Смоук-тест на то, что реальные проблемы конфига действительно ловятся,
    а не просто в теории должны ловиться (см. Приложение проверок ТЗ §4.2)."""

    def _load_mutated(self, tmp_path, mutate):
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
        mutate(raw)
        p = tmp_path / 'mutated.yaml'
        p.write_text(yaml.dump(raw, allow_unicode=True), encoding='utf-8')
        return p

    def test_unknown_field_rejected(self, tmp_path):
        p = self._load_mutated(tmp_path, lambda r: r['filters'][0].__setitem__('bogus', 1))
        with pytest.raises(Exception):
            load_config(p)

    def test_price_ceiling_enforced(self, tmp_path):
        p = self._load_mutated(tmp_path, lambda r: r['filters'][0].__setitem__('price_max', 5_000_000))
        with pytest.raises(ConfigError):
            load_config(p)

    def test_forbidden_region_rejected(self, tmp_path):
        p = self._load_mutated(
            tmp_path,
            lambda r: r['region_presets']['CORE'].append('Новосибирская область'),
        )
        with pytest.raises(ConfigError):
            load_config(p)

    def test_yo_in_keyword_rejected(self, tmp_path):
        p = self._load_mutated(tmp_path, lambda r: r['filters'][0]['keywords'].append('шуруповёрт'))
        with pytest.raises(ConfigError):
            load_config(p)

    def test_slash_in_keyword_rejected(self, tmp_path):
        p = self._load_mutated(tmp_path, lambda r: r['filters'][0]['keywords'].append('а/б'))
        with pytest.raises(ConfigError):
            load_config(p)

    def test_duplicate_slug_rejected(self, tmp_path):
        p = self._load_mutated(
            tmp_path,
            lambda r: r['filters'].append(copy.deepcopy(r['filters'][0])),
        )
        with pytest.raises(ConfigError):
            load_config(p)
