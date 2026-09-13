"""Загрузчик и резолвер декларативного конфига фильтров (filters_v2.yaml).

Только парсинг + валидация + разворачивание пресетов в готовую структуру.
БД не трогает (см. tender_sniper/filters/apply.py — импортёр) и не меняет
поведение матчера (см. tender_sniper/matching/smart_matcher.py — правки
normalize_yo / keyword_wins_over_exclusion там, отдельно от этого модуля).

См. docs/filters_v2_discovery.md и TZ_filters_v2.md за обоснованием формата.
"""

from pathlib import Path
from typing import Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ConfigError(Exception):
    """Структурная или семантическая ошибка конфига — никогда не проглатывается молча."""


# ============================================================
# Сырые модели — форма ровно как в YAML, до применения defaults
# и разворачивания пресетов
# ============================================================

class MatchingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    normalize_yo: bool
    normalize_case: bool
    collapse_whitespace: bool
    match_mode: str
    keyword_wins_over_exclusion: bool
    search_fields: List[str]


class Defaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    price_min: Optional[float] = None
    price_max: float
    price_max_soft: Optional[float] = None
    object_type: str
    regions_preset: str
    exclusion_presets: List[str] = Field(default_factory=list)
    status: str = 'active'


class GroupEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    title: str
    owner: str


class FilterEntry(BaseModel):
    """Форма фильтра как в YAML — большинство полей опциональны и наследуют
    defaults, если не заданы явно (см. load_config)."""
    model_config = ConfigDict(extra='forbid')
    slug: str
    name: str
    group: str
    wave: int
    legacy: List[str] = Field(default_factory=list)
    regions_preset: str
    object_type: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    nacrejim: Optional[str] = None
    keywords: List[str]
    # None = наследовать defaults.exclusion_presets; [] = явно без пресетов
    # (см. work-maintenance в конфиге) — оба случая различимы, поэтому Optional,
    # а не просто пустой список по умолчанию.
    exclusion_presets: Optional[List[str]] = None
    exclusions: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    status: Optional[str] = None


class DeprecatedEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    name: str
    reason: str


class RawConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int
    generated_at: str
    replaces_registry: str
    defaults: Defaults
    matching: MatchingConfig
    region_presets: Dict[str, List[str]]
    regions_never: List[str]
    exclusion_presets: Dict[str, List[str]]
    groups: List[GroupEntry]
    filters: List[FilterEntry]
    deprecated: List[DeprecatedEntry]
    migration_map: Dict[str, List[str]]


# ============================================================
# Резолвленные модели — всё финальное, без пресетов/ссылок
# ============================================================

class ResolvedFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slug: str
    name: str
    group: str
    owner: str
    wave: int
    legacy: List[str]
    regions_preset: str
    regions: List[str]
    object_type: str
    price_min: Optional[float]
    price_max: float
    nacrejim: Optional[str]
    keywords: List[str]
    exclusions: List[str]
    notes: Optional[str]
    status: str


class ResolvedConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int
    matching: MatchingConfig
    regions_never: List[str]
    filters: List[ResolvedFilter]
    deprecated: List[DeprecatedEntry]
    migration_map: Dict[str, List[str]]


def _resolve_region_preset(
    name: str,
    raw_presets: Dict[str, List[str]],
    resolving: Optional[frozenset] = None,
) -> List[str]:
    """Разворачивает `@include:OTHER` внутри пресета регионов (недокументированная
    в ТЗ директива, используемая только в WIDE_URAL — см. открытые вопросы
    discovery-отчёта). Не встретил нигде в валидаторе — реализовано по
    очевидному прочтению синтаксиса, а не угадано вслепую."""
    resolving = resolving or frozenset()
    if name in resolving:
        raise ConfigError(f"циклическая ссылка @include в пресете регионов: {name}")
    if name not in raw_presets:
        raise ConfigError(f"неизвестный пресет регионов: {name}")
    resolving = resolving | {name}

    out: List[str] = []
    for entry in raw_presets[name]:
        if isinstance(entry, str) and entry.startswith('@include:'):
            included = entry.split(':', 1)[1]
            out.extend(_resolve_region_preset(included, raw_presets, resolving))
        else:
            out.append(entry)
    return out


def load_config(path: Union[str, Path]) -> ResolvedConfig:
    """Загружает, валидирует и полностью разворачивает filters_v2.yaml.

    Бросает ConfigError на любую структурную/семантическую проблему
    (неизвестный пресет, запрещённый регион, потолок цены выше глобального,
    ё/слэш в ключевике, слишком короткий ключевик, дубль slug, битая
    migration_map) — портирует проверки validate_filters.py как жёсткие
    ошибки загрузки, а не отдельный lint-скрипт, плюс pydantic ловит любое
    незнакомое поле как ошибку схемы (extra='forbid' везде).
    """
    path = Path(path)
    raw_yaml = yaml.safe_load(path.read_text(encoding='utf-8'))
    raw = RawConfig.model_validate(raw_yaml)

    resolved_regions = {
        name: _resolve_region_preset(name, raw.region_presets)
        for name in raw.region_presets
    }

    never = set(raw.regions_never)
    for name, regions in resolved_regions.items():
        forbidden = never & set(regions)
        if forbidden:
            raise ConfigError(f"пресет {name}: запрещённые регионы {sorted(forbidden)}")

    known_groups = {g.id for g in raw.groups}
    group_owner = {g.id: g.owner for g in raw.groups}

    slugs_seen: set = set()
    resolved_filters: List[ResolvedFilter] = []

    for f in raw.filters:
        if f.slug in slugs_seen:
            raise ConfigError(f"дубль slug: {f.slug}")
        slugs_seen.add(f.slug)

        if f.group not in known_groups:
            raise ConfigError(f"{f.slug}: неизвестная группа {f.group}")
        if f.regions_preset not in resolved_regions:
            raise ConfigError(f"{f.slug}: неизвестный пресет регионов {f.regions_preset}")

        object_type = f.object_type or raw.defaults.object_type
        price_min = f.price_min if f.price_min is not None else raw.defaults.price_min
        price_max = f.price_max if f.price_max is not None else raw.defaults.price_max
        if price_max > raw.defaults.price_max:
            raise ConfigError(f"{f.slug}: потолок {price_max} > {raw.defaults.price_max}")

        exclusion_preset_names = (
            f.exclusion_presets if f.exclusion_presets is not None
            else raw.defaults.exclusion_presets
        )
        expanded_exclusions: List[str] = []
        for p in exclusion_preset_names:
            if p not in raw.exclusion_presets:
                raise ConfigError(f"{f.slug}: неизвестный пресет исключений {p}")
            expanded_exclusions.extend(raw.exclusion_presets[p])
        expanded_exclusions.extend(f.exclusions)

        seen_ex: set = set()
        deduped_exclusions: List[str] = []
        for e in expanded_exclusions:
            if e not in seen_ex:
                seen_ex.add(e)
                deduped_exclusions.append(e)

        for kw in f.keywords:
            if kw != kw.lower():
                raise ConfigError(f"{f.slug}: ключевик не в нижнем регистре: {kw}")
            if 'ё' in kw:
                raise ConfigError(f"{f.slug}: ё в ключевике: {kw}")
            if '/' in kw:
                raise ConfigError(f"{f.slug}: слэш в ключевике: {kw}")
            if len(kw) < 3:
                raise ConfigError(f"{f.slug}: слишком короткий ключевик: {kw}")
        for ex in deduped_exclusions:
            if 'ё' in ex:
                raise ConfigError(f"{f.slug}: ё в исключении: {ex}")

        resolved_filters.append(ResolvedFilter(
            slug=f.slug,
            name=f.name,
            group=f.group,
            owner=group_owner[f.group],
            wave=f.wave,
            legacy=f.legacy,
            regions_preset=f.regions_preset,
            regions=resolved_regions[f.regions_preset],
            object_type=object_type,
            price_min=price_min,
            price_max=price_max,
            nacrejim=f.nacrejim,
            keywords=f.keywords,
            exclusions=deduped_exclusions,
            notes=f.notes,
            status=f.status or raw.defaults.status,
        ))

    for old, news in raw.migration_map.items():
        for n in news:
            if n not in slugs_seen:
                raise ConfigError(f"migration_map {old}: нет slug {n}")

    return ResolvedConfig(
        version=raw.version,
        matching=raw.matching,
        regions_never=raw.regions_never,
        filters=resolved_filters,
        deprecated=raw.deprecated,
        migration_map=raw.migration_map,
    )
