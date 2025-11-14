"""
Level 2 Analyzer: Chain-of-Thought + Verification Loop
Реализует двухэтапный процесс:
1. Extraction с CoT reasoning
2. Verification каждого извлеченного факта
"""

import json
from typing import Dict, Any, Optional
from loguru import logger

from src.models.level2_models import (
    NMCKExtraction,
    DeadlineExtraction,
    GuaranteeExtraction,
    RequirementsExtraction,
    VerificationResult,
    VerifiedParameter,
    TenderAnalysisResult,
    ConfidenceLevel,
    VerificationStatus,
    ChainOfThought,
    ReasoningStep
)
from src.utils.level2.openai_client import OpenAIClient
from src.utils.level2.prompts import (
    EXTRACT_NMCK_PROMPT,
    EXTRACT_DEADLINE_PROMPT,
    EXTRACT_GUARANTEE_PROMPT,
    EXTRACT_REQUIREMENTS_PROMPT,
    format_extraction_prompt,
    format_verification_prompt
)
from pydantic import ValidationError


class Level2Analyzer:
    """
    Анализатор уровня 2: Extraction + Verification
    
    Особенности:
    - Chain-of-Thought reasoning для каждого параметра
    - Автоматическая верификация извлеченных данных
    - Retry логика при обнаружении ошибок
    - Структурированные Pydantic модели
    """
    
    def __init__(self, openai_client: OpenAIClient, max_retries: int = 2):
        """
        Args:
            openai_client: Клиент OpenAI API
            max_retries: Максимум попыток при ошибках верификации
        """
        self.llm = openai_client
        self.max_retries = max_retries
        logger.info("Level2Analyzer initialized")
    
    # ============= MAIN ANALYSIS METHOD =============
    
    def analyze_tender(
        self,
        document: str,
        parameters: Optional[list] = None
    ) -> TenderAnalysisResult:
        """
        Полный анализ тендера
        
        Args:
            document: Текст тендерной документации
            parameters: Список параметров для извлечения (если None — все)
        
        Returns:
            TenderAnalysisResult с верифицированными параметрами
        """
        logger.info("Starting Level 2 tender analysis")
        
        # Параметры по умолчанию
        if parameters is None:
            parameters = [
                'nmck',
                'deadline_submission',
                'deadline_execution',
                'application_guarantee',
                'contract_guarantee',
                'technical_requirements'
            ]
        
        result = TenderAnalysisResult()
        total_verifications = 0
        high_confidence_count = 0
        issues_count = 0
        
        # Извлекаем и верифицируем каждый параметр
        for param in parameters:
            logger.info(f"Processing parameter: {param}")
            
            try:
                verified_param = self._extract_and_verify_parameter(
                    document=document,
                    parameter_name=param
                )
                
                # Сохраняем результат
                setattr(result, param, verified_param)
                
                # Обновляем статистику
                total_verifications += 1
                if verified_param.final_confidence == ConfidenceLevel.HIGH:
                    high_confidence_count += 1
                if verified_param.verification.issues:
                    issues_count += len(verified_param.verification.issues)
                
                logger.info(
                    f"✓ {param}: {verified_param.verification.status.value}, "
                    f"confidence: {verified_param.final_confidence.value}"
                )
                
            except Exception as e:
                logger.error(f"✗ Failed to process {param}: {e}")
                issues_count += 1
        
        # Обновляем мета-информацию
        result.total_verifications = total_verifications
        result.high_confidence_count = high_confidence_count
        result.issues_found = issues_count
        
        logger.info(
            f"Analysis complete: {high_confidence_count}/{total_verifications} high confidence, "
            f"{issues_count} issues"
        )
        
        return result
    
    # ============= EXTRACTION + VERIFICATION =============
    
    def _extract_and_verify_parameter(
        self,
        document: str,
        parameter_name: str
    ) -> VerifiedParameter:
        """
        Извлечение и верификация одного параметра
        
        Process:
        1. Extract с CoT reasoning
        2. Verify
        3. If verification failed → retry extraction
        4. Return final verified result
        """
        # Этап 1: Извлечение
        logger.debug(f"Step 1: Extracting {parameter_name}")
        extracted = self._extract_parameter_with_cot(document, parameter_name)
        
        # Этап 2: Верификация
        logger.debug(f"Step 2: Verifying {parameter_name}")
        verification = self._verify_extraction(document, extracted, parameter_name)
        
        # Этап 3: Retry если нужно
        retry_count = 0
        while (
            verification.status in [VerificationStatus.REJECTED, VerificationStatus.CORRECTED]
            and retry_count < self.max_retries
        ):
            logger.warning(
                f"Verification {verification.status.value} for {parameter_name}, "
                f"retrying... ({retry_count + 1}/{self.max_retries})"
            )
            
            # Переизвлекаем с учетом найденных проблем
            extracted = self._retry_extraction(
                document,
                parameter_name,
                verification.issues
            )
            
            # Проверяем снова
            verification = self._verify_extraction(document, extracted, parameter_name)
            retry_count += 1
        
        # Определяем финальное значение
        final_value = extracted
        if verification.status == VerificationStatus.CORRECTED and verification.corrected_value:
            final_value = verification.corrected_value
        
        # Определяем финальную уверенность
        final_confidence = self._determine_final_confidence(verification)
        
        return VerifiedParameter(
            parameter_name=parameter_name,
            extracted_value=extracted,
            verification=verification,
            final_value=final_value,
            final_confidence=final_confidence
        )
    
    # ============= EXTRACTION METHODS =============
    
    def _extract_parameter_with_cot(
        self,
        document: str,
        parameter_name: str
    ) -> Dict[str, Any]:
        """
        Извлечение параметра с Chain-of-Thought
        """
        # Выбираем промпт в зависимости от параметра
        prompt = self._get_extraction_prompt(document, parameter_name)
        
        # Запрашиваем у LLM (JSON response)
        try:
            response = self.llm.query_json(
                prompt=prompt,
                system_prompt="Ты эксперт по анализу тендерной документации. Работай методично и точно."
            )
            
            # Валидация с Pydantic
            validated = self._validate_extraction(response, parameter_name)
            
            return validated
            
        except Exception as e:
            logger.error(f"Extraction failed for {parameter_name}: {e}")
            raise
    
    def _get_extraction_prompt(self, document: str, parameter_name: str) -> str:
        """Получить промпт для извлечения параметра"""
        
        # Ограничиваем размер документа для контекста
        doc_truncated = document[:40000]
        
        prompts_map = {
            'nmck': EXTRACT_NMCK_PROMPT,
            'deadline_submission': EXTRACT_DEADLINE_PROMPT,
            'deadline_execution': EXTRACT_DEADLINE_PROMPT,
            'application_guarantee': EXTRACT_GUARANTEE_PROMPT,
            'contract_guarantee': EXTRACT_GUARANTEE_PROMPT,
            'technical_requirements': EXTRACT_REQUIREMENTS_PROMPT
        }
        
        template = prompts_map.get(parameter_name)
        if not template:
            raise ValueError(f"Unknown parameter: {parameter_name}")
        
        # Форматируем промпт с дополнительными параметрами
        kwargs = {}
        if 'deadline' in parameter_name:
            deadline_type = "подачи заявок" if "submission" in parameter_name else "исполнения контракта"
            kwargs['deadline_type'] = deadline_type
        if 'guarantee' in parameter_name:
            guarantee_type = "заявки" if "application" in parameter_name else "контракта"
            kwargs['guarantee_type'] = guarantee_type
        
        return format_extraction_prompt(template, doc_truncated, **kwargs)
    
    def _validate_extraction(
        self,
        response: Dict[str, Any],
        parameter_name: str
    ) -> Dict[str, Any]:
        """Валидация извлеченных данных через Pydantic"""
        
        try:
            if parameter_name == 'nmck':
                # Создаем полный ChainOfThought объект
                cot = self._parse_chain_of_thought(response.get('reasoning', {}))
                result = response.get('result', {})
                result['reasoning'] = cot
                validated = NMCKExtraction(**result)
            
            elif 'deadline' in parameter_name:
                cot = self._parse_chain_of_thought(response.get('reasoning', {}))
                result = response.get('result', {})
                result['reasoning'] = cot
                validated = DeadlineExtraction(**result)
            
            elif 'guarantee' in parameter_name:
                cot = self._parse_chain_of_thought(response.get('reasoning', {}))
                result = response.get('result', {})
                result['reasoning'] = cot
                validated = GuaranteeExtraction(**result)
            
            elif parameter_name == 'technical_requirements':
                cot = self._parse_chain_of_thought(response.get('reasoning', {}))
                result = response.get('result', {})
                result['reasoning'] = cot
                validated = RequirementsExtraction(**result)
            
            else:
                raise ValueError(f"Unknown parameter for validation: {parameter_name}")
            
            return validated.model_dump()
            
        except ValidationError as e:
            logger.error(f"Validation error for {parameter_name}: {e}")
            raise
    
    def _parse_chain_of_thought(self, reasoning_dict: Dict[str, Any]) -> ChainOfThought:
        """Парсинг Chain-of-Thought из ответа LLM"""
        steps = []
        
        # Извлекаем шаги
        for i, (key, value) in enumerate(reasoning_dict.items(), start=1):
            if isinstance(value, dict):
                step = ReasoningStep(
                    step_number=i,
                    description=value.get('description', key),
                    findings=value.get('findings', value.get('found_sections', [])),
                    analysis=value.get('analysis', value.get('details', str(value)))
                )
                steps.append(step)
        
        # Извлекаем заключение
        conclusion = reasoning_dict.get('step5', {}).get('analysis', '')
        if not conclusion:
            conclusion = "Анализ завершен"
        
        return ChainOfThought(steps=steps, conclusion=conclusion)
    
    # ============= VERIFICATION METHODS =============
    
    def _verify_extraction(
        self,
        document: str,
        extracted: Dict[str, Any],
        parameter_name: str
    ) -> VerificationResult:
        """
        Верификация извлеченных данных
        """
        prompt = format_verification_prompt(
            extracted_data=extracted,
            document=document[:40000],
            parameter_name=parameter_name
        )
        
        try:
            response = self.llm.query_json(
                prompt=prompt,
                system_prompt="Ты критически настроенный инспектор. Проверяй КАЖДОЕ утверждение."
            )
            
            # Парсим результат верификации
            verification = VerificationResult(**response)
            
            return verification
            
        except Exception as e:
            logger.error(f"Verification failed for {parameter_name}: {e}")
            # Возвращаем дефолтный результат с ошибкой
            return VerificationResult(
                status=VerificationStatus.NEEDS_REVIEW,
                evidence=[],
                confidence=ConfidenceLevel.LOW,
                issues=[f"Verification error: {str(e)}"],
                reasoning="Верификация не выполнена из-за ошибки"
            )
    
    def _retry_extraction(
        self,
        document: str,
        parameter_name: str,
        issues: list
    ) -> Dict[str, Any]:
        """
        Повторное извлечение с учетом найденных проблем
        """
        logger.info(f"Retrying extraction for {parameter_name} with {len(issues)} issues")
        
        # Базовый промпт
        base_prompt = self._get_extraction_prompt(document, parameter_name)
        
        # Добавляем информацию о проблемах
        issues_text = "\n".join([f"- {issue}" for issue in issues])
        
        enhanced_prompt = f"""{base_prompt}

ВНИМАНИЕ! При предыдущей попытке были найдены следующие проблемы:
{issues_text}

Учти эти проблемы и будь особенно внимателен к этим аспектам.
"""
        
        try:
            response = self.llm.query_json(
                prompt=enhanced_prompt,
                system_prompt="Ты эксперт по анализу тендеров. Учитывай найденные ошибки."
            )
            
            validated = self._validate_extraction(response, parameter_name)
            return validated
            
        except Exception as e:
            logger.error(f"Retry extraction failed: {e}")
            raise
    
    # ============= HELPER METHODS =============
    
    def _determine_final_confidence(
        self,
        verification: VerificationResult
    ) -> ConfidenceLevel:
        """Определение финальной уверенности на основе верификации"""
        
        if verification.status == VerificationStatus.CONFIRMED:
            return verification.confidence
        
        elif verification.status == VerificationStatus.CORRECTED:
            # Если исправлено — уверенность снижается
            if verification.confidence == ConfidenceLevel.HIGH:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.LOW
        
        elif verification.status == VerificationStatus.REJECTED:
            return ConfidenceLevel.LOW
        
        else:  # NEEDS_REVIEW
            return ConfidenceLevel.LOW
    
    def get_analysis_summary(self, result: TenderAnalysisResult) -> Dict[str, Any]:
        """
        Получить краткую сводку анализа
        """
        summary = result.get_summary()
        
        # Добавляем детали по каждому параметру
        parameters_summary = {}
        for param_name in ['nmck', 'deadline_submission', 'deadline_execution',
                          'application_guarantee', 'contract_guarantee', 'technical_requirements']:
            param = getattr(result, param_name, None)
            if param:
                parameters_summary[param_name] = {
                    'status': param.verification.status.value,
                    'confidence': param.final_confidence.value,
                    'issues_count': len(param.verification.issues)
                }
        
        summary['parameters'] = parameters_summary
        
        return summary
