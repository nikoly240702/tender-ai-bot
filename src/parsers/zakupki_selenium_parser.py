"""
Парсер zakupki.gov.ru с использованием Selenium.
Используется для обхода защиты от ботов и парсинга динамических страниц.

ТРЕБУЕТСЯ:
    pip install selenium webdriver-manager
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from typing import List, Dict, Any, Optional
import time
import re


class ZakupkiSeleniumParser:
    """
    Парсер zakupki.gov.ru с использованием Selenium.
    Обходит защиту от ботов и работает с динамическим контентом.
    """

    BASE_URL = "https://zakupki.gov.ru"
    SEARCH_URL = f"{BASE_URL}/epz/order/extendedsearch/results.html"

    def __init__(self, headless: bool = True, timeout: int = 30):
        """
        Инициализация Selenium парсера.

        Args:
            headless: Запускать браузер в headless режиме (без GUI)
            timeout: Таймаут ожидания элементов (секунды)
        """
        self.headless = headless
        self.timeout = timeout
        self.driver = None

    def __enter__(self):
        """Context manager entry."""
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def _init_driver(self):
        """Инициализирует Chrome WebDriver."""
        print("🚀 Запуск Chrome WebDriver...")

        chrome_options = Options()

        if self.headless:
            chrome_options.add_argument("--headless")

        # Опции для обхода детекции ботов
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")

        # User-Agent
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Отключаем уведомления и всплывающие окна
        chrome_options.add_experimental_option(
            "prefs", {
                "profile.default_content_setting_values.notifications": 2
            }
        )

        # Скрываем признаки webdriver
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        # Инициализация драйвера
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

        # Удаляем признаки webdriver через JavaScript
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })

        self.driver.set_page_load_timeout(self.timeout)
        print("✓ WebDriver запущен")

    def search_tenders(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        page_limit: int = 3,
        delay: float = 2.0
    ) -> List[Dict[str, Any]]:
        """
        Ищет тендеры на zakupki.gov.ru.

        Args:
            keywords: Ключевые слова для поиска
            price_min: Минимальная цена контракта (руб)
            price_max: Максимальная цена контракта (руб)
            page_limit: Максимальное количество страниц
            delay: Задержка между запросами страниц (секунды)

        Returns:
            Список найденных тендеров
        """
        if not self.driver:
            self._init_driver()

        print(f"🔍 Поиск тендеров на zakupki.gov.ru (Selenium)...")
        print(f"   Критерии: {keywords or 'все'}, {price_min}-{price_max} руб")

        tenders = []

        try:
            # Формируем URL с параметрами
            search_url = self._build_search_url(keywords, price_min, price_max)

            # Переходим на страницу поиска
            print(f"   Загрузка страницы поиска...")
            self.driver.get(search_url)

            # Ждем загрузки результатов
            time.sleep(3)  # Даем время на прогрузку

            # Парсим страницы результатов
            for page_num in range(1, page_limit + 1):
                print(f"   Страница {page_num}/{page_limit}...")

                # Ждем появления карточек тендеров
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CLASS_NAME, "search-registry-entry-block")
                        )
                    )
                except:
                    print(f"   ⚠️  Карточки тендеров не найдены на странице {page_num}")
                    break

                # Извлекаем тендеры с текущей страницы
                page_tenders = self._extract_tenders_from_page()
                tenders.extend(page_tenders)

                print(f"   Найдено на странице: {len(page_tenders)}")

                # Переходим на следующую страницу (если не последняя)
                if page_num < page_limit:
                    if not self._go_to_next_page():
                        print("   ℹ️  Больше страниц нет")
                        break
                    time.sleep(delay)

            print(f"✓ Всего найдено тендеров: {len(tenders)}")

        except Exception as e:
            print(f"✗ Ошибка поиска: {e}")

        return tenders

    def _build_search_url(
        self,
        keywords: Optional[str],
        price_min: Optional[int],
        price_max: Optional[int]
    ) -> str:
        """Формирует URL для поиска с параметрами."""
        params = {
            'morphology': 'on',
            'search-filter': 'Дате+размещения',
            'pageNumber': '1',
            'sortDirection': 'false',
            'recordsPerPage': '_10',
            'showLotsInfoHidden': 'false',
            'sortBy': 'UPDATE_DATE',
            'fz44': 'on',
            'fz223': 'on',
            'af': 'on',
            'currencyIdGeneral': '-1'
        }

        if keywords:
            params['searchString'] = keywords
        if price_min:
            params['priceFromGeneral'] = str(price_min)
        if price_max:
            params['priceToGeneral'] = str(price_max)

        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        return f"{self.SEARCH_URL}?{query_string}"

    def _extract_tenders_from_page(self) -> List[Dict[str, Any]]:
        """Извлекает тендеры с текущей загруженной страницы."""
        tenders = []

        try:
            # Находим все карточки тендеров
            tender_cards = self.driver.find_elements(
                By.CLASS_NAME, "search-registry-entry-block"
            )

            for card in tender_cards:
                tender = self._parse_tender_card(card)
                if tender:
                    tenders.append(tender)

        except Exception as e:
            print(f"   Ошибка извлечения тендеров: {e}")

        return tenders

    def _parse_tender_card(self, card) -> Optional[Dict[str, Any]]:
        """Парсит одну карточку тендера."""
        try:
            tender = {}

            # Номер закупки и ссылка
            try:
                number_elem = card.find_element(
                    By.CSS_SELECTOR, ".registry-entry__header-mid__number a"
                )
                tender['number'] = number_elem.text.strip()
                tender['url'] = number_elem.get_attribute('href')
            except:
                pass

            # Название
            try:
                name_elem = card.find_element(
                    By.CLASS_NAME, "registry-entry__body-value"
                )
                tender['name'] = name_elem.text.strip()
            except:
                pass

            # Цена
            try:
                price_elem = card.find_element(By.CLASS_NAME, "price-block__value")
                price_text = price_elem.text.strip()
                tender['price_formatted'] = price_text
                tender['price'] = self._extract_price(price_text)
            except:
                pass

            # Заказчик
            try:
                customer_elem = card.find_element(
                    By.CLASS_NAME, "registry-entry__body-href"
                )
                tender['customer'] = customer_elem.text.strip()
            except:
                pass

            # Статус
            try:
                status_elem = card.find_element(
                    By.CLASS_NAME, "registry-entry__header-mid__title"
                )
                tender['status'] = status_elem.text.strip()
            except:
                pass

            return tender if tender.get('number') else None

        except Exception as e:
            return None

    def _extract_price(self, price_text: str) -> Optional[float]:
        """Извлекает числовое значение цены из текста."""
        try:
            cleaned = re.sub(r'[^\d,.]', '', price_text)
            cleaned = cleaned.replace(',', '.').replace(' ', '')
            return float(cleaned)
        except:
            return None

    def _go_to_next_page(self) -> bool:
        """Переходит на следующую страницу результатов."""
        try:
            # Ищем кнопку "Следующая страница"
            next_button = self.driver.find_element(
                By.CSS_SELECTOR, "a.paginator__ctrl[aria-label='Следующая страница']"
            )

            if next_button and next_button.is_enabled():
                next_button.click()
                time.sleep(2)  # Ждем загрузки
                return True

        except:
            pass

        return False

    def get_tender_details(self, tender_url: str) -> Dict[str, Any]:
        """
        Получает детальную информацию о тендере.

        Args:
            tender_url: URL страницы тендера

        Returns:
            Детальная информация
        """
        if not self.driver:
            self._init_driver()

        try:
            print(f"📄 Загрузка деталей тендера...")
            self.driver.get(tender_url)
            time.sleep(2)

            details = {
                'url': tender_url,
                'full_content': self.driver.page_source
            }

            # Можно добавить более детальный парсинг здесь

            return details

        except Exception as e:
            print(f"✗ Ошибка получения деталей: {e}")
            return {}

    def close(self):
        """Закрывает WebDriver."""
        if self.driver:
            print("🛑 Закрытие WebDriver...")
            self.driver.quit()
            self.driver = None


def main():
    """Пример использования Selenium парсера."""
    print("\n" + "="*70)
    print("ТЕСТ SELENIUM ПАРСЕРА ZAKUPKI.GOV.RU")
    print("="*70)

    # Используем context manager для автоматического закрытия браузера
    with ZakupkiSeleniumParser(headless=True) as parser:
        tenders = parser.search_tenders(
            keywords="компьютерное оборудование",
            price_min=500000,
            price_max=5000000,
            page_limit=2
        )

        print(f"\n📊 Результаты:")
        print(f"   Найдено тендеров: {len(tenders)}\n")

        for i, tender in enumerate(tenders[:5], 1):
            print(f"{i}. {tender.get('name', 'Без названия')[:80]}")
            print(f"   Номер: {tender.get('number', 'N/A')}")
            print(f"   Цена: {tender.get('price_formatted', 'N/A')}")
            print(f"   Заказчик: {tender.get('customer', 'N/A')[:60]}")
            print(f"   URL: {tender.get('url', 'N/A')}")
            print()


if __name__ == "__main__":
    main()
