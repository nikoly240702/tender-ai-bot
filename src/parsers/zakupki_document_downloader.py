"""
Модуль для автоматического извлечения и скачивания документов из карточек тендеров zakupki.gov.ru.
"""

from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
import re
from pathlib import Path
import time
import warnings

# Отключаем предупреждения SSL
warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except:
    pass


class ZakupkiDocumentDownloader:
    """
    Автоматический загрузчик документов из карточек тендеров zakupki.gov.ru.
    """

    def __init__(self, download_dir: Optional[Path] = None):
        """
        Инициализация загрузчика.

        Args:
            download_dir: Директория для сохранения документов
        """
        self.download_dir = download_dir or Path.cwd() / 'downloaded_tenders'
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

        # Типы документов, которые нас интересуют
        self.document_types = {
            'tech_spec': ['технического задания', 'техническое задание', 'тз'],
            'contract': ['проект контракта', 'проект договора', 'договор'],
            'notice': ['извещение', 'документация'],
            'specification': ['спецификация', 'смета'],
        }

    def get_tender_documents(self, tender_url: str, tender_number: str) -> List[Dict[str, Any]]:
        """
        Извлекает список документов из карточки тендера.

        Args:
            tender_url: URL карточки тендера (относительный или полный)
            tender_number: Номер тендера для создания папки

        Returns:
            Список словарей с информацией о документах
        """
        # Формируем URL вкладки "Документы"
        # Определяем тип тендера из URL
        tender_type = None
        if '/notice/zk20/' in tender_url or '/notice/zk20/' in tender_url:
            tender_type = 'zk20'
        elif '/notice/ea20/' in tender_url or '/notice/ea44/' in tender_url:
            tender_type = 'ea20'
        elif '/notice/ok20/' in tender_url or '/notice/ok44/' in tender_url:
            tender_type = 'ok20'
        else:
            # Пытаемся извлечь из URL
            match = re.search(r'/notice/([^/]+)/', tender_url)
            if match:
                tender_type = match.group(1)

        if not tender_type:
            print(f"   ⚠️  Не удалось определить тип тендера из URL")
            tender_type = 'zk20'  # По умолчанию

        # Формируем URL вкладки документов
        docs_url = f'https://zakupki.gov.ru/epz/order/notice/{tender_type}/view/documents.html?regNumber={tender_number}'

        print(f"\n📄 Извлечение документов из тендера {tender_number}...")
        print(f"   🔗 URL документов: {docs_url}")

        try:
            # Загружаем страницу с документами
            response = self.session.get(docs_url, timeout=30, verify=False)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Ищем раздел с документами
            documents = []

            # Метод 1: Ищем контейнеры с классом 'attachment' (специфично для zakupki.gov.ru)
            doc_containers = soup.find_all('div', class_='attachment')

            print(f"   📦 Найдено контейнеров документов: {len(doc_containers)}")

            for container in doc_containers:
                # Ищем ссылку на скачивание внутри контейнера
                doc_link = container.find('a', href=re.compile(r'44fz/filestore|223fz/filestore'))

                if doc_link:
                    href = doc_link.get('href', '')
                    text = doc_link.get_text(strip=True)

                    # Формируем полный URL
                    if href.startswith('//'):
                        doc_url = f'https:{href}'
                    elif href.startswith('/'):
                        doc_url = f'https://zakupki.gov.ru{href}'
                    elif not href.startswith('http'):
                        doc_url = f'https://zakupki.gov.ru/{href}'
                    else:
                        doc_url = href

                    # Определяем тип документа
                    doc_type = self._classify_document(text, href)

                    # Извлекаем имя файла из текста
                    filename = self._extract_filename_from_text(text, doc_type)

                    documents.append({
                        'url': doc_url,
                        'filename': filename,
                        'title': text or filename,
                        'type': doc_type,
                        'extension': self._get_extension(filename)
                    })

            # Метод 2: Ищем прямые ссылки на файлы (запасной вариант)
            if not documents:
                print(f"   🔄 Ищем прямые ссылки на файлы...")
                doc_links = soup.find_all('a', href=re.compile(r'\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|rtf)$', re.IGNORECASE))

                seen_urls = set()

                for link in doc_links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)

                    # Пропускаем дубликаты
                    if href in seen_urls:
                        continue

                    # Проверяем, что это документ
                    if not self._is_document_link(href):
                        continue

                    seen_urls.add(href)

                    # Формируем полный URL
                    if href.startswith('//'):
                        doc_url = f'https:{href}'
                    elif href.startswith('/'):
                        doc_url = f'https://zakupki.gov.ru{href}'
                    elif not href.startswith('http'):
                        doc_url = f'https://zakupki.gov.ru/{href}'
                    else:
                        doc_url = href

                    # Определяем тип документа
                    doc_type = self._classify_document(text, href)

                    # Извлекаем имя файла
                    filename = self._extract_filename(doc_url, text)

                    documents.append({
                        'url': doc_url,
                        'filename': filename,
                        'title': text or filename,
                        'type': doc_type,
                        'extension': self._get_extension(filename)
                    })

            print(f"   ✅ Найдено документов: {len(documents)}")

            # Выводим информацию о найденных документах
            for i, doc in enumerate(documents, 1):
                print(f"      {i}. [{doc['type']}] {doc['title'][:60]} ({doc['extension']})")

            return documents

        except Exception as e:
            print(f"   ❌ Ошибка при извлечении документов: {e}")
            return []

    def download_documents(
        self,
        tender_url: str,
        tender_number: str,
        doc_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Скачивает документы из карточки тендера.

        Args:
            tender_url: URL карточки тендера
            tender_number: Номер тендера
            doc_types: Типы документов для скачивания (None = все)

        Returns:
            Словарь с результатами загрузки
        """
        # Получаем список документов
        documents = self.get_tender_documents(tender_url, tender_number)

        if not documents:
            return {
                'tender_number': tender_number,
                'total_documents': 0,
                'downloaded': 0,
                'failed': 0,
                'files': []
            }

        # Фильтруем по типам, если указано
        if doc_types:
            documents = [doc for doc in documents if doc['type'] in doc_types]

        # Создаем папку для тендера
        tender_dir = self.download_dir / self._sanitize_filename(tender_number)
        tender_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n💾 Скачивание документов в: {tender_dir}")

        # Скачиваем документы
        downloaded_files = []
        failed = 0

        for i, doc in enumerate(documents, 1):
            print(f"   [{i}/{len(documents)}] Скачивание: {doc['title'][:50]}...")

            try:
                # Скачиваем файл
                response = self.session.get(doc['url'], timeout=60, verify=False)
                response.raise_for_status()

                # Сохраняем файл
                file_path = tender_dir / doc['filename']

                with open(file_path, 'wb') as f:
                    f.write(response.content)

                file_size = len(response.content) / 1024  # KB

                downloaded_files.append({
                    'filename': doc['filename'],
                    'path': str(file_path),
                    'title': doc['title'],
                    'type': doc['type'],
                    'size_kb': round(file_size, 2)
                })

                print(f"      ✅ Сохранено: {doc['filename']} ({file_size:.1f} KB)")

                # Небольшая задержка между запросами
                time.sleep(0.5)

            except Exception as e:
                print(f"      ❌ Ошибка: {e}")
                failed += 1

        print(f"\n✅ Скачивание завершено:")
        print(f"   📥 Скачано: {len(downloaded_files)}")
        print(f"   ❌ Ошибок: {failed}")

        return {
            'tender_number': tender_number,
            'tender_dir': str(tender_dir),
            'total_documents': len(documents),
            'downloaded': len(downloaded_files),
            'failed': failed,
            'files': downloaded_files
        }

    def _is_document_link(self, href: str) -> bool:
        """Проверяет, является ли ссылка документом."""
        doc_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.7z', '.rtf']
        href_lower = href.lower()
        return any(href_lower.endswith(ext) for ext in doc_extensions)

    def _classify_document(self, text: str, url: str) -> str:
        """Классифицирует документ по названию."""
        text_lower = text.lower()
        url_lower = url.lower()
        combined = f"{text_lower} {url_lower}"

        for doc_type, keywords in self.document_types.items():
            if any(keyword in combined for keyword in keywords):
                return doc_type

        return 'other'

    def _extract_filename(self, url: str, title: str) -> str:
        """Извлекает имя файла из URL или названия."""
        # Пытаемся извлечь из URL
        from urllib.parse import urlparse, unquote

        parsed = urlparse(url)
        path = unquote(parsed.path)

        if path:
            filename = Path(path).name
            if filename and '.' in filename:
                return self._sanitize_filename(filename)

        # Если не получилось, создаем из названия
        if title:
            # Убираем недопустимые символы
            clean_title = re.sub(r'[^\w\s\-.]', '', title)
            clean_title = re.sub(r'\s+', '_', clean_title)

            # Добавляем расширение, если его нет
            if '.' not in clean_title:
                # Пытаемся определить из URL
                ext = self._get_extension(url)
                clean_title = f"{clean_title}.{ext}"

            return clean_title[:100]  # Ограничиваем длину

        # Последняя попытка - генерируем имя
        return f"document_{int(time.time())}.pdf"

    def _get_extension(self, filename: str) -> str:
        """Извлекает расширение файла."""
        match = re.search(r'\.([a-z0-9]+)$', filename.lower())
        return match.group(1) if match else 'unknown'

    def _sanitize_filename(self, filename: str) -> str:
        """Очищает имя файла от недопустимых символов."""
        # Убираем недопустимые символы для Windows/Unix
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Убираем множественные подчеркивания
        filename = re.sub(r'_+', '_', filename)
        return filename.strip('_')

    def _extract_filename_from_text(self, text: str, doc_type: str) -> str:
        """Создает имя файла из текста описания документа."""
        # Очищаем текст
        clean_text = re.sub(r'[^\w\s\-.]', '', text)
        clean_text = re.sub(r'\s+', '_', clean_text)
        clean_text = clean_text[:80]  # Ограничиваем длину

        # Добавляем расширение по типу
        ext_map = {
            'tech_spec': '.pdf',
            'contract': '.pdf',
            'notice': '.pdf',
            'specification': '.pdf',
            'other': '.pdf'
        }

        ext = ext_map.get(doc_type, '.pdf')

        if not clean_text.endswith(ext):
            clean_text = f"{clean_text}{ext}"

        return self._sanitize_filename(clean_text)


def main():
    """Пример использования."""
    print("\n" + "="*70)
    print("  ТЕСТ ЗАГРУЗЧИКА ДОКУМЕНТОВ")
    print("="*70 + "\n")

    # Создаем загрузчик
    downloader = ZakupkiDocumentDownloader()

    # Пример: скачиваем документы из тендера
    # Нужен реальный URL тендера для теста
    test_tender_url = "/epz/order/notice/ea44/view/common-info.html?regNumber=0173100002023000123"
    test_tender_number = "0173100002023000123"

    print("⚠️  Внимание: Для теста нужен реальный URL тендера с zakupki.gov.ru")
    print(f"Пример URL: {test_tender_url}\n")

    # Получаем список документов (без скачивания)
    documents = downloader.get_tender_documents(
        f"https://zakupki.gov.ru{test_tender_url}",
        test_tender_number
    )

    if documents:
        print("\n" + "="*70)
        print("  НАЙДЕННЫЕ ДОКУМЕНТЫ")
        print("="*70 + "\n")

        for i, doc in enumerate(documents, 1):
            print(f"{i}. {doc['title']}")
            print(f"   Тип: {doc['type']}")
            print(f"   Файл: {doc['filename']}")
            print(f"   URL: {doc['url'][:80]}...")
            print()

        # Спрашиваем, скачивать ли
        response = input("Скачать документы? (y/n): ")
        if response.lower() == 'y':
            result = downloader.download_documents(
                f"https://zakupki.gov.ru{test_tender_url}",
                test_tender_number
            )

            print("\n" + "="*70)
            print("  РЕЗУЛЬТАТ ЗАГРУЗКИ")
            print("="*70 + "\n")
            print(f"📁 Папка: {result['tender_dir']}")
            print(f"📥 Скачано: {result['downloaded']} из {result['total_documents']}")
            print(f"❌ Ошибок: {result['failed']}")
    else:
        print("\n❌ Документы не найдены. Проверьте URL тендера.")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()
