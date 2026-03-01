"""
Document Generator — автогенерация тендерных документов.

Генерирует пакет документов (заявка, декларация, согласие, техпредложение)
на основе шаблонов DOCX и данных компании.
"""

from .generator import DocumentGenerator

__all__ = ['DocumentGenerator']
