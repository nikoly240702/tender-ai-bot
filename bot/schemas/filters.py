"""
Pydantic schemas для валидации пользовательского ввода.

Обеспечивает безопасность и чистоту данных в системе.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import re
import logging

logger = logging.getLogger(__name__)


# ============================================
# ВАЛИДНЫЕ КОДЫ ОКПД2 (первые 2 цифры)
# ============================================
VALID_OKPD2_PREFIXES = {
    '01', '02', '03',  # Сельское хозяйство, лесное, рыболовство
    '05', '06', '07', '08', '09',  # Добыча полезных ископаемых
    '10', '11', '12', '13', '14', '15', '16', '17', '18', '19',  # Продукты питания, текстиль
    '20', '21', '22', '23', '24', '25', '26', '27', '28', '29',  # Химия, электроника, машины
    '30', '31', '32', '33',  # Транспорт, мебель, ремонт
    '35', '36', '37', '38', '39',  # Электроэнергия, вода, отходы
    '41', '42', '43',  # Строительство
    '45', '46', '47',  # Торговля
    '49', '50', '51', '52', '53',  # Транспорт, почта
    '55', '56',  # Гостиницы, общепит
    '58', '59', '60', '61', '62', '63',  # ИТ, телеком, СМИ
    '64', '65', '66',  # Финансы
    '68', '69', '70', '71', '72', '73', '74', '75',  # Недвижимость, наука, консалтинг
    '77', '78', '79',  # Аренда, персонал, туризм
    '80', '81', '82',  # Охрана, уборка, админ услуги
    '84', '85', '86', '87', '88',  # Госуправление, образование, здравоохранение
    '90', '91', '92', '93', '94', '95', '96',  # Культура, спорт, прочее
    '97', '98', '99'  # Домашние хоз-ва, международные организации
}


class FilterCreate(BaseModel):
    """Схема для создания фильтра."""

    name: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="Название фильтра"
    )
    keywords: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Ключевые слова для поиска"
    )
    price_min: Optional[int] = Field(
        None,
        ge=0,
        le=1_000_000_000,
        description="Минимальная цена тендера"
    )
    price_max: Optional[int] = Field(
        None,
        ge=0,
        le=1_000_000_000,
        description="Максимальная цена тендера"
    )
    regions: Optional[List[str]] = Field(
        None,
        max_length=100,  # В России 89 регионов, даём запас
        description="Регионы для фильтрации"
    )
    okpd2_codes: Optional[List[str]] = Field(
        None,
        max_length=50,
        description="Коды ОКПД2 для фильтрации"
    )

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Валидация названия фильтра."""
        # Запрещаем HTML теги
        if '<' in v or '>' in v:
            raise ValueError('HTML теги запрещены в названии')

        # Запрещаем SQL ключевые слова
        sql_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'SELECT', '--', ';']
        v_upper = v.upper()
        for keyword in sql_keywords:
            if keyword in v_upper:
                raise ValueError(f'Недопустимое ключевое слово: {keyword}')

        # Убираем лишние пробелы
        v = ' '.join(v.split())

        return v.strip()

    @field_validator('keywords')
    @classmethod
    def validate_keywords(cls, v: List[str]) -> List[str]:
        """Валидация ключевых слов."""
        # Фильтруем пустые строки и убираем лишние пробелы
        keywords = [kw.strip() for kw in v if kw.strip()]

        if not keywords:
            raise ValueError('Необходимо указать хотя бы одно ключевое слово')

        if len(keywords) > 20:
            raise ValueError('Максимум 20 ключевых слов')

        # Проверяем каждое ключевое слово
        for kw in keywords:
            if len(kw) < 2:
                raise ValueError(f'Ключевое слово слишком короткое: "{kw}" (минимум 2 символа)')
            if len(kw) > 100:
                raise ValueError(f'Ключевое слово слишком длинное: "{kw}" (максимум 100 символов)')
            if '<' in kw or '>' in kw:
                raise ValueError(f'HTML теги запрещены в ключевых словах: "{kw}"')

        return keywords

    @field_validator('regions')
    @classmethod
    def validate_regions(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """
        Валидация регионов с проверкой на существование.

        Использует модуль tender_sniper.regions для проверки валидности.
        """
        if v is None:
            return None

        # Фильтруем пустые строки
        regions = [r.strip() for r in v if r.strip()]

        if not regions:
            return None

        # Проверяем каждый регион
        validated_regions = []
        invalid_regions = []

        for region in regions:
            # Проверяем HTML теги
            if '<' in region or '>' in region:
                raise ValueError(f'HTML теги запрещены в регионах: "{region}"')

            # Пробуем валидировать через модуль регионов
            try:
                from tender_sniper.regions import find_region
                recognized = find_region(region)
                if recognized:
                    validated_regions.append(recognized)
                else:
                    # Если не найден точно, сохраняем оригинал (мягкая валидация)
                    logger.warning(f"Регион не распознан точно: {region}")
                    validated_regions.append(region)
            except ImportError:
                # Если модуль недоступен, просто проверяем базово
                validated_regions.append(region)

        return validated_regions if validated_regions else None

    @field_validator('okpd2_codes')
    @classmethod
    def validate_okpd2_codes(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """
        Валидация кодов ОКПД2.

        Проверяет формат кода (XX или XX.XX или XX.XX.XX) и
        что первые 2 цифры входят в допустимый диапазон.
        """
        if v is None:
            return None

        # Фильтруем пустые строки
        codes = [c.strip() for c in v if c.strip()]

        if not codes:
            return None

        validated_codes = []
        okpd_pattern = re.compile(r'^(\d{2})(?:\.(\d{1,2})(?:\.(\d{1,3}))?)?$')

        for code in codes:
            # Проверяем формат
            match = okpd_pattern.match(code)
            if not match:
                raise ValueError(
                    f'Некорректный формат ОКПД2: "{code}". '
                    f'Ожидается формат: XX, XX.XX или XX.XX.XX (например: 26, 26.20, 26.20.1)'
                )

            # Проверяем что первые 2 цифры валидны
            prefix = match.group(1)
            if prefix not in VALID_OKPD2_PREFIXES:
                raise ValueError(
                    f'Несуществующий код ОКПД2: "{code}". '
                    f'Код {prefix} не входит в классификатор ОКПД2'
                )

            validated_codes.append(code)

        return validated_codes if validated_codes else None

    @model_validator(mode='after')
    def validate_price_range(self):
        """Проверка диапазона цен."""
        if self.price_min is not None and self.price_max is not None:
            if self.price_min > self.price_max:
                raise ValueError('Минимальная цена не может быть больше максимальной')

        return self


class FilterUpdate(BaseModel):
    """Схема для обновления фильтра."""

    name: Optional[str] = Field(
        None,
        min_length=3,
        max_length=100
    )
    keywords: Optional[List[str]] = Field(
        None,
        min_length=1,
        max_length=20
    )
    price_min: Optional[int] = Field(
        None,
        ge=0,
        le=1_000_000_000
    )
    price_max: Optional[int] = Field(
        None,
        ge=0,
        le=1_000_000_000
    )
    regions: Optional[List[str]] = Field(
        None,
        max_length=100  # В России 89 регионов, даём запас
    )
    okpd2_codes: Optional[List[str]] = Field(
        None,
        max_length=50
    )
    is_active: Optional[bool] = None

    # Используем те же валидаторы
    _validate_name = field_validator('name')(FilterCreate.validate_name.__func__)
    _validate_keywords = field_validator('keywords')(FilterCreate.validate_keywords.__func__)
    _validate_regions = field_validator('regions')(FilterCreate.validate_regions.__func__)
    _validate_okpd2 = field_validator('okpd2_codes')(FilterCreate.validate_okpd2_codes.__func__)

    @model_validator(mode='after')
    def validate_at_least_one_field(self):
        """Проверка что хотя бы одно поле указано для обновления."""
        if not any([
            self.name,
            self.keywords,
            self.price_min is not None,
            self.price_max is not None,
            self.regions,
            self.okpd2_codes,
            self.is_active is not None
        ]):
            raise ValueError('Необходимо указать хотя бы одно поле для обновления')

        return self


class SearchQuery(BaseModel):
    """Схема для поискового запроса."""

    query: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Поисковый запрос"
    )
    tender_type: Optional[str] = Field(
        None,
        description="Тип тендера (товары/услуги)"
    )
    price_min: Optional[int] = Field(
        None,
        ge=0,
        le=1_000_000_000
    )
    price_max: Optional[int] = Field(
        None,
        ge=0,
        le=1_000_000_000
    )
    regions: Optional[List[str]] = Field(
        None,
        max_length=10
    )

    @field_validator('query')
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Валидация поискового запроса."""
        # Запрещаем HTML теги
        if '<' in v or '>' in v:
            raise ValueError('HTML теги запрещены в запросе')

        # Убираем лишние пробелы
        v = ' '.join(v.split())

        return v.strip()

    @field_validator('tender_type')
    @classmethod
    def validate_tender_type(cls, v: Optional[str]) -> Optional[str]:
        """Валидация типа тендера."""
        if v is None:
            return None

        allowed_types = ['товары', 'услуги', 'работы', 'all']
        if v.lower() not in allowed_types:
            raise ValueError(f'Недопустимый тип тендера. Разрешены: {", ".join(allowed_types)}')

        return v.lower()

    @model_validator(mode='after')
    def validate_price_range(self):
        """Проверка диапазона цен."""
        if self.price_min is not None and self.price_max is not None:
            if self.price_min > self.price_max:
                raise ValueError('Минимальная цена не может быть больше максимальной')

        return self


def sanitize_html(text: str) -> str:
    """
    Очистка HTML от потенциально опасных элементов.

    Args:
        text: Исходный текст

    Returns:
        Безопасный текст с экранированными HTML символами
    """
    if not text:
        return text

    # Экранируем HTML символы
    html_escape_table = {
        "&": "&amp;",
        '"': "&quot;",
        "'": "&#x27;",
        ">": "&gt;",
        "<": "&lt;",
    }

    return "".join(html_escape_table.get(c, c) for c in text)


__all__ = [
    'FilterCreate',
    'FilterUpdate',
    'SearchQuery',
    'sanitize_html'
]
