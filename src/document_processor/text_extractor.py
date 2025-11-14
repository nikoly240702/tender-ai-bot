"""
Модуль для извлечения текста из документов тендерной документации.
Поддерживает форматы: PDF, DOCX, ZIP.
"""

import os
from typing import Optional
from pathlib import Path
import PyPDF2
from docx import Document
import subprocess
import zipfile
import tempfile
import shutil


class TextExtractor:
    """Класс для извлечения текста из различных форматов документов."""

    @staticmethod
    def detect_file_type(file_path: str) -> str:
        """
        Определяет реальный тип файла по содержимому (magic bytes), а не по расширению.
        Использует системную команду 'file' для определения типа.

        Args:
            file_path: Путь к файлу

        Returns:
            Тип файла: 'pdf', 'docx', 'doc', или 'unknown'
        """
        try:
            # Используем системную команду 'file' для определения типа
            result = subprocess.run(
                ['file', '--brief', '--mime-type', file_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                mime_type = result.stdout.strip().lower()

                # Определяем тип по MIME
                if 'pdf' in mime_type:
                    return 'pdf'
                elif 'wordprocessingml' in mime_type or 'vnd.openxmlformats' in mime_type:
                    return 'docx'
                elif 'msword' in mime_type or 'ms-word' in mime_type:
                    return 'doc'
                elif 'composite' in mime_type or 'ole' in mime_type:
                    # Старые .doc файлы (OLE Compound Document)
                    return 'doc'
                elif 'zip' in mime_type or 'x-zip' in mime_type:
                    return 'zip'

            # Если не удалось определить через file, пробуем альтернативный метод
            result2 = subprocess.run(
                ['file', '--brief', file_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result2.returncode == 0:
                file_desc = result2.stdout.strip().lower()

                if 'pdf' in file_desc:
                    return 'pdf'
                elif 'microsoft word 2007' in file_desc or 'microsoft ooxml' in file_desc:
                    return 'docx'
                elif 'microsoft office document' in file_desc or 'composite document' in file_desc:
                    return 'doc'

            return 'unknown'

        except Exception as e:
            print(f"⚠️  Не удалось определить тип файла {file_path}: {e}")
            # Fallback: используем расширение
            ext = Path(file_path).suffix.lower()
            if ext == '.pdf':
                return 'pdf'
            elif ext in ['.docx', '.doc']:
                return 'docx'
            return 'unknown'

    @staticmethod
    def extract_from_pdf_with_ocr(file_path: str, max_pages: int = 20) -> str:
        """
        Извлекает текст из PDF используя OCR (для сканированных документов).

        Args:
            file_path: Путь к PDF файлу
            max_pages: Максимальное количество страниц для OCR (чтобы не перегружать систему)

        Returns:
            Извлеченный текст

        Raises:
            Exception: При ошибках OCR
        """
        try:
            from pdf2image import convert_from_path
            import pytesseract
            from PIL import Image

            print(f"   🔍 Используем OCR для распознавания сканированного PDF...")

            # Конвертируем PDF в изображения
            try:
                images = convert_from_path(file_path, first_page=1, last_page=max_pages)
            except Exception as e:
                print(f"   ⚠️  Ошибка конвертации PDF в изображения: {e}")
                raise

            if not images:
                raise ValueError("Не удалось конвертировать PDF в изображения")

            print(f"   📄 Обработка {len(images)} страниц через OCR...")

            # Применяем OCR к каждому изображению
            text_content = []
            for i, image in enumerate(images, 1):
                try:
                    # Распознаем текст на русском и английском
                    text = pytesseract.image_to_string(image, lang='rus+eng')
                    if text.strip():
                        text_content.append(text.strip())
                    print(f"   ✓ Страница {i}/{len(images)} обработана")
                except Exception as page_error:
                    print(f"   ⚠️  Ошибка OCR на странице {i}: {page_error}")
                    continue

            extracted_text = '\n\n'.join(text_content)

            if not extracted_text.strip():
                raise ValueError("OCR не смог извлечь текст из PDF")

            print(f"   ✅ OCR завершен, извлечено {len(extracted_text)} символов")
            return extracted_text

        except ImportError as ie:
            missing_lib = str(ie).split("'")[1] if "'" in str(ie) else "библиотека"
            raise Exception(f"Для OCR требуется установить {missing_lib}: pip install pdf2image pytesseract pillow")
        except Exception as e:
            raise Exception(f"Ошибка OCR: {str(e)}")

    @staticmethod
    def extract_from_pdf(file_path: str) -> str:
        """
        Извлекает текст из PDF файла.
        Сначала пытается извлечь обычным способом (PyPDF2),
        если не удается - использует OCR для сканированных документов.

        Args:
            file_path: Путь к PDF файлу

        Returns:
            Извлеченный текст

        Raises:
            FileNotFoundError: Если файл не найден
            Exception: При ошибках чтения PDF
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        try:
            text_content = []
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)

                for page_num in range(total_pages):
                    page = pdf_reader.pages[page_num]
                    text = page.extract_text()
                    if text.strip():
                        text_content.append(text)

            extracted_text = '\n\n'.join(text_content)

            if not extracted_text.strip():
                # PDF не содержит текста - вероятно это скан
                # Пробуем OCR
                print(f"   ⚠️  PDF не содержит текстового слоя, пробуем OCR...")
                try:
                    return TextExtractor.extract_from_pdf_with_ocr(file_path)
                except Exception as ocr_error:
                    print(f"   ❌ OCR также не удался: {ocr_error}")
                    raise ValueError("PDF файл не содержит извлекаемого текста и OCR не помог")

            return extracted_text

        except Exception as e:
            # Если это не ошибка "нет текста", просто пробрасываем исключение
            if "не содержит извлекаемого текста" in str(e) or "EOF marker not found" in str(e):
                # Пробуем OCR как последнюю попытку
                try:
                    print(f"   ⚠️  Ошибка извлечения текста ({str(e)}), пробуем OCR...")
                    return TextExtractor.extract_from_pdf_with_ocr(file_path)
                except Exception as ocr_error:
                    print(f"   ❌ OCR также не удался: {ocr_error}")

            raise Exception(f"Ошибка при извлечении текста из PDF: {str(e)}")

    @staticmethod
    def extract_from_docx(file_path: str) -> str:
        """
        Извлекает текст из DOCX файла.
        Поддерживает как новые (.docx), так и старые (.doc) форматы Word.

        Args:
            file_path: Путь к DOCX/DOC файлу

        Returns:
            Извлеченный текст

        Raises:
            FileNotFoundError: Если файл не найден
            Exception: При ошибках чтения DOCX
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        try:
            doc = Document(file_path)
            text_content = []

            # Извлекаем текст из параграфов
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text_content.append(paragraph.text)

            # Извлекаем текст из таблиц
            for table in doc.tables:
                for row in table.rows:
                    row_text = []
                    for cell in row.cells:
                        if cell.text.strip():
                            row_text.append(cell.text.strip())
                    if row_text:
                        text_content.append(' | '.join(row_text))

            extracted_text = '\n\n'.join(text_content)

            if not extracted_text.strip():
                raise ValueError("DOCX файл не содержит текста")

            return extracted_text

        except Exception as e:
            # Fallback: пытаемся извлечь текст через antiword для старых .doc файлов
            try:
                print(f"   ⚠️  Ошибка python-docx, пытаемся использовать antiword...")
                result = subprocess.run(
                    ['antiword', file_path],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    print(f"   ✅ Текст извлечен через antiword")
                    return result.stdout.strip()
            except Exception as antiword_error:
                print(f"   ⚠️  antiword тоже не помог: {antiword_error}")

            # Fallback 2: пытаемся textract (если установлен)
            try:
                import textract
                print(f"   ⚠️  Пытаемся использовать textract...")
                text = textract.process(file_path).decode('utf-8')
                if text.strip():
                    print(f"   ✅ Текст извлечен через textract")
                    return text.strip()
            except ImportError:
                pass
            except Exception as textract_error:
                print(f"   ⚠️  textract не помог: {textract_error}")

            # Если все попытки провалились, возвращаем частичную информацию
            print(f"   ❌ Не удалось извлечь текст из документа")
            raise Exception(f"Ошибка при извлечении текста из DOCX: {str(e)}")

    @staticmethod
    def extract_from_zip(file_path: str) -> str:
        """
        Извлекает текст из ZIP-архива, распаковывая его содержимое.
        Автоматически определяет DOCX файлы (которые тоже являются ZIP).

        Args:
            file_path: Путь к ZIP файлу

        Returns:
            Извлеченный текст из всех поддерживаемых файлов в архиве
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        try:
            # Сначала проверяем, не является ли это DOCX файлом
            # DOCX это ZIP архив с определенной структурой (содержит word/document.xml)
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                file_list = zip_ref.namelist()

                # Проверяем признаки DOCX/Office Open XML
                if 'word/document.xml' in file_list or '[Content_Types].xml' in file_list:
                    print(f"   📦 Обнаружен Office документ (DOCX) внутри ZIP")
                    # Это DOCX файл, используем python-docx для извлечения
                    try:
                        doc = Document(file_path)
                        text_content = []

                        # Извлекаем текст из параграфов
                        for paragraph in doc.paragraphs:
                            if paragraph.text.strip():
                                text_content.append(paragraph.text)

                        # Извлекаем текст из таблиц
                        for table in doc.tables:
                            for row in table.rows:
                                row_text = []
                                for cell in row.cells:
                                    if cell.text.strip():
                                        row_text.append(cell.text.strip())
                                if row_text:
                                    text_content.append(' | '.join(row_text))

                        extracted_text = '\n\n'.join(text_content)

                        if not extracted_text.strip():
                            raise ValueError("DOCX файл не содержит текста")

                        return extracted_text
                    except Exception as e:
                        raise Exception(f"Ошибка извлечения текста из DOCX в ZIP: {str(e)}")

            # Если это обычный ZIP с файлами (не DOCX)
            with tempfile.TemporaryDirectory() as temp_dir:
                # Распаковываем архив
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                # Получаем список всех файлов (исключая служебные)
                extracted_files = []
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        # Пропускаем XML и служебные файлы DOCX
                        if (not file.startswith('.') and
                            not file.startswith('__') and
                            not file.endswith('.xml') and
                            not file.endswith('.rels') and
                            not file.endswith('.bin')):
                            extracted_files.append(os.path.join(root, file))

                if not extracted_files:
                    # Попробуем все файлы, включая XML
                    for root, dirs, files in os.walk(temp_dir):
                        for file in files:
                            if not file.startswith('.') and not file.startswith('__'):
                                extracted_files.append(os.path.join(root, file))

                if not extracted_files:
                    raise ValueError("ZIP архив пустой или содержит только служебные файлы")

                print(f"   📦 Найдено файлов в архиве: {len(extracted_files)}")

                # Извлекаем текст из каждого файла
                all_texts = []
                for extracted_file in extracted_files:
                    try:
                        # Рекурсивно вызываем extract_text для каждого файла
                        result = TextExtractor.extract_text(extracted_file)
                        if result['text'] and not result['text'].startswith('[Не удалось'):
                            all_texts.append(f"=== {result['file_name']} ===\n{result['text']}")
                    except Exception as e:
                        # Просто пропускаем файлы, которые не удалось обработать
                        continue

                if not all_texts:
                    raise ValueError("Не удалось извлечь текст ни из одного файла в архиве")

                return '\n\n'.join(all_texts)

        except zipfile.BadZipFile:
            raise Exception("Файл поврежден или не является корректным ZIP архивом")
        except Exception as e:
            raise Exception(f"Ошибка при извлечении текста из ZIP: {str(e)}")

    @staticmethod
    def extract_text(file_path: str) -> dict:
        """
        Универсальный метод для извлечения текста из поддерживаемых форматов.

        Args:
            file_path: Путь к файлу

        Returns:
            Словарь с извлеченным текстом и метаданными:
            {
                'text': str,
                'file_name': str,
                'file_type': str,
                'char_count': int,
                'word_count': int
            }

        Raises:
            ValueError: Если формат не поддерживается
            Exception: При ошибках извлечения
        """
        file_path = str(Path(file_path).resolve())

        # ВАЖНО: Определяем реальный тип файла по содержимому, а не по расширению!
        # Zakupki.gov.ru часто называет Word файлы с расширением .pdf
        actual_type = TextExtractor.detect_file_type(file_path)

        print(f"   📄 Файл: {Path(file_path).name}")
        print(f"   🔍 Определен тип: {actual_type}")

        # Определяем метод извлечения по РЕАЛЬНОМУ типу файла
        try:
            if actual_type == 'pdf':
                text = TextExtractor.extract_from_pdf(file_path)
                file_type = 'PDF'
            elif actual_type in ['docx', 'doc']:
                text = TextExtractor.extract_from_docx(file_path)
                file_type = 'DOCX/DOC'
            elif actual_type == 'zip':
                text = TextExtractor.extract_from_zip(file_path)
                file_type = 'ZIP'
            else:
                file_extension = Path(file_path).suffix.lower()
                print(f"   ⚠️  Неподдерживаемый формат: {actual_type}")
                # Возвращаем пустой результат вместо исключения
                text = f"[Не удалось извлечь текст: неподдерживаемый формат {actual_type}]"
                file_type = 'UNKNOWN'
        except Exception as extract_error:
            # Если не удалось извлечь текст, возвращаем информацию об ошибке
            print(f"   ❌ Ошибка извлечения текста: {extract_error}")
            text = f"[Не удалось извлечь текст из файла: {str(extract_error)[:200]}]"
            file_type = actual_type.upper()

        # Подсчитываем статистику
        char_count = len(text)
        word_count = len(text.split())

        return {
            'text': text,
            'file_name': os.path.basename(file_path),
            'file_type': file_type,
            'char_count': char_count,
            'word_count': word_count
        }

    @staticmethod
    def extract_from_multiple_files(file_paths: list) -> dict:
        """
        Извлекает текст из нескольких файлов и объединяет их.

        Args:
            file_paths: Список путей к файлам

        Returns:
            Словарь с объединенным текстом и метаданными:
            {
                'combined_text': str,
                'files': list[dict],
                'total_char_count': int,
                'total_word_count': int
            }
        """
        results = []
        combined_text = []

        for file_path in file_paths:
            try:
                result = TextExtractor.extract_text(file_path)
                results.append(result)
                combined_text.append(f"=== {result['file_name']} ===\n{result['text']}")
            except Exception as e:
                results.append({
                    'file_name': os.path.basename(file_path),
                    'error': str(e)
                })

        full_text = '\n\n'.join(combined_text)

        return {
            'combined_text': full_text,
            'files': results,
            'total_char_count': len(full_text),
            'total_word_count': len(full_text.split())
        }


def main():
    """Пример использования TextExtractor."""
    import sys

    if len(sys.argv) < 2:
        print("Использование: python text_extractor.py <путь_к_файлу>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        result = TextExtractor.extract_text(file_path)
        print(f"\n{'='*60}")
        print(f"Файл: {result['file_name']}")
        print(f"Тип: {result['file_type']}")
        print(f"Символов: {result['char_count']:,}")
        print(f"Слов: {result['word_count']:,}")
        print(f"{'='*60}\n")
        print(result['text'][:500] + "..." if len(result['text']) > 500 else result['text'])
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
