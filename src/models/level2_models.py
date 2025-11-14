"""
Pydantic модели для структурированного анализа тендеров
Обеспечивают типизацию и валидацию данных на каждом этапе
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum


# ============= ENUMS =============

class ConfidenceLevel(str, Enum):
    """Уровень уверенности в извлеченных данных"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class VerificationStatus(str, Enum):
    """Статус верификации"""
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Currency(str, Enum):
    """Валюты"""
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    CNY = "CNY"


# ============= REASONING MODELS =============

class ReasoningStep(BaseModel):
    """Один шаг рассуждения (Chain-of-Thought)"""
    step_number: int = Field(..., ge=1, description="Номер шага")
    description: str = Field(..., min_length=10, description="Описание шага")
    findings: List[str] = Field(default_factory=list, description="Найденные данные на этом шаге")
    analysis: str = Field(..., min_length=10, description="Анализ найденных данных")


class ChainOfThought(BaseModel):
    """Chain-of-Thought reasoning для одного параметра"""
    steps: List[ReasoningStep] = Field(..., min_items=1, description="Шаги рассуждения")
    conclusion: str = Field(..., min_length=20, description="Итоговый вывод")
    
    class Config:
        json_schema_extra = {
            "example": {
                "steps": [
                    {
                        "step_number": 1,
                        "description": "Поиск раздела с ценой",
                        "findings": ["Раздел 3.1: Цена контракта", "Приложение №1: Спецификация"],
                        "analysis": "Цена указана в двух местах, необходимо проверить согласованность"
                    }
                ],
                "conclusion": "НМЦК составляет 5 000 000 руб., включая НДС"
            }
        }


# ============= EXTRACTION MODELS =============

class NMCKExtraction(BaseModel):
    """Извлеченная НМЦК"""
    value: float = Field(..., gt=0, description="Значение НМЦК")
    currency: Currency = Field(default=Currency.RUB, description="Валюта")
    vat_included: bool = Field(..., description="Включен ли НДС")
    source: str = Field(..., min_length=5, description="Источник (раздел документа)")
    confidence: ConfidenceLevel = Field(..., description="Уровень уверенности")
    reasoning: ChainOfThought = Field(..., description="Рассуждения по извлечению")
    
    @validator('value')
    def validate_realistic_price(cls, v):
        """Проверка реалистичности цены"""
        if v > 10_000_000_000:  # 10 млрд рублей
            raise ValueError(f"НМЦК подозрительно большая: {v}")
        if v < 1000:  # 1000 рублей
            raise ValueError(f"НМЦК подозрительно маленькая: {v}")
        return v


class DeadlineExtraction(BaseModel):
    """Извлеченный срок"""
    datetime_str: str = Field(..., description="Дата и время в формате ISO 8601")
    timezone: str = Field(default="MSK", description="Часовой пояс")
    description: str = Field(..., description="Описание (подача заявок, выполнение работ и т.д.)")
    source: str = Field(..., description="Источник")
    confidence: ConfidenceLevel = Field(..., description="Уровень уверенности")
    reasoning: ChainOfThought = Field(..., description="Рассуждения")
    
    @validator('datetime_str')
    def validate_datetime_format(cls, v):
        """Проверка формата даты"""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Неверный формат даты: {v}. Ожидается ISO 8601")
        return v


class GuaranteeExtraction(BaseModel):
    """Извлеченное обеспечение"""
    amount: Optional[float] = Field(None, ge=0, description="Сумма обеспечения")
    percentage: Optional[float] = Field(None, ge=0, le=100, description="Процент от НМЦК")
    type: str = Field(..., description="Тип обеспечения (заявки, контракта)")
    source: str = Field(..., description="Источник")
    confidence: ConfidenceLevel = Field(..., description="Уровень уверенности")
    reasoning: ChainOfThought = Field(..., description="Рассуждения")
    
    @validator('amount', 'percentage')
    def validate_one_specified(cls, v, values):
        """Хотя бы один параметр должен быть указан"""
        if v is None and values.get('amount') is None and values.get('percentage') is None:
            raise ValueError("Должна быть указана либо сумма, либо процент")
        return v


class TechnicalRequirement(BaseModel):
    """Техническое требование"""
    requirement: str = Field(..., min_length=10, description="Описание требования")
    category: str = Field(..., description="Категория (сертификация, опыт, лицензии и т.д.)")
    is_mandatory: bool = Field(..., description="Обязательное ли требование")
    source: str = Field(..., description="Источник")


class RequirementsExtraction(BaseModel):
    """Извлеченные требования"""
    requirements: List[TechnicalRequirement] = Field(..., min_items=0)
    confidence: ConfidenceLevel = Field(..., description="Уровень уверенности")
    reasoning: ChainOfThought = Field(..., description="Рассуждения")


# ============= VERIFICATION MODELS =============

class VerificationEvidence(BaseModel):
    """Доказательства при верификации"""
    quote: str = Field(..., min_length=10, description="Прямая цитата из документа")
    source: str = Field(..., description="Источник цитаты")
    page_number: Optional[int] = Field(None, description="Номер страницы (если доступен)")


class Contradiction(BaseModel):
    """Обнаруженное противоречие"""
    description: str = Field(..., description="Описание противоречия")
    value1: Any = Field(..., description="Первое значение")
    source1: str = Field(..., description="Источник первого значения")
    value2: Any = Field(..., description="Второе значение")
    source2: str = Field(..., description="Источник второго значения")
    severity: Literal["CRITICAL", "MAJOR", "MINOR"] = Field(..., description="Серьезность")


class VerificationResult(BaseModel):
    """Результат верификации"""
    status: VerificationStatus = Field(..., description="Статус верификации")
    evidence: List[VerificationEvidence] = Field(..., description="Доказательства")
    contradictions: List[Contradiction] = Field(default_factory=list, description="Противоречия")
    corrected_value: Optional[Any] = Field(None, description="Исправленное значение (если статус CORRECTED)")
    confidence: ConfidenceLevel = Field(..., description="Итоговый уровень уверенности")
    issues: List[str] = Field(default_factory=list, description="Выявленные проблемы")
    reasoning: str = Field(..., min_length=20, description="Рассуждения верификатора")


# ============= FINAL RESULTS =============

class VerifiedParameter(BaseModel):
    """Верифицированный параметр"""
    parameter_name: str = Field(..., description="Название параметра")
    extracted_value: Any = Field(..., description="Извлеченное значение")
    verification: VerificationResult = Field(..., description="Результат верификации")
    final_value: Any = Field(..., description="Финальное значение после верификации")
    final_confidence: ConfidenceLevel = Field(..., description="Финальный уровень уверенности")


class TenderAnalysisResult(BaseModel):
    """Итоговый результат анализа тендера"""
    nmck: Optional[VerifiedParameter] = None
    deadline_submission: Optional[VerifiedParameter] = None
    deadline_execution: Optional[VerifiedParameter] = None
    application_guarantee: Optional[VerifiedParameter] = None
    contract_guarantee: Optional[VerifiedParameter] = None
    technical_requirements: Optional[VerifiedParameter] = None
    
    # Мета-информация
    analysis_timestamp: datetime = Field(default_factory=datetime.now)
    total_verifications: int = Field(default=0, description="Всего верификаций проведено")
    high_confidence_count: int = Field(default=0, description="Параметров с высокой уверенностью")
    issues_found: int = Field(default=0, description="Обнаружено проблем")
    
    def get_summary(self) -> Dict[str, Any]:
        """Получить краткую сводку"""
        return {
            "total_parameters": sum(1 for p in [
                self.nmck, self.deadline_submission, self.deadline_execution,
                self.application_guarantee, self.contract_guarantee, self.technical_requirements
            ] if p is not None),
            "high_confidence": self.high_confidence_count,
            "issues": self.issues_found,
            "timestamp": self.analysis_timestamp.isoformat()
        }


# ============= ERROR MODELS =============

class AnalysisError(BaseModel):
    """Ошибка при анализе"""
    error_type: str = Field(..., description="Тип ошибки")
    message: str = Field(..., description="Сообщение об ошибке")
    parameter: Optional[str] = Field(None, description="Параметр, при обработке которого произошла ошибка")
    timestamp: datetime = Field(default_factory=datetime.now)
    traceback: Optional[str] = Field(None, description="Traceback (если доступен)")
