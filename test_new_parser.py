#!/usr/bin/env python3
"""
Тест обновленного парсера документов.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_document_downloader import ZakupkiDocumentDownloader

def main():
    print("\n" + "="*70)
    print("  ТЕСТ ОБНОВЛЕННОГО ПАРСЕРА ДОКУМЕНТОВ")
    print("="*70 + "\n")

    downloader = ZakupkiDocumentDownloader()

    # Тестируем на тендерах, где мы видели документы
    test_tenders = [
        {
            'url': '/epz/order/notice/zk20/view/common-info.html?regNumber=0322200027425000278',
            'number': '0322200027425000278',
            'name': 'Запрос котировок (бумага)'
        },
        {
            'url': '/epz/order/notice/ea20/view/common-info.html?regNumber=0352100025025000104',
            'number': '0352100025025000104',
            'name': 'Электронный аукцион (бумага)'
        }
    ]

    for tender in test_tenders:
        print(f"\n{'─'*70}")
        print(f"ТЕНДЕР: {tender['name']}")
        print(f"{'─'*70}")

        # Получаем список документов
        documents = downloader.get_tender_documents(
            tender_url=tender['url'],
            tender_number=tender['number']
        )

        if documents:
            print(f"\n✅ Найдено {len(documents)} документов:\n")
            for i, doc in enumerate(documents, 1):
                print(f"{i}. {doc['title']}")
                print(f"   Тип: {doc['type']}")
                print(f"   Файл: {doc['filename']}")
                print(f"   URL: {doc['url'][:80]}...")
                print()

            # Предлагаем скачать
            print("💡 Документы найдены и готовы к скачиванию!")
            print(f"   Для скачивания используйте: downloader.download_documents(...)")

        else:
            print(f"\n❌ Документы не найдены")

    print("\n" + "="*70)
    print("✅ ТЕСТ ЗАВЕРШЕН")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
