#!/usr/bin/env python3
"""
Отображает результаты AI-анализа из JSON файлов.
"""

import json
import sys
from pathlib import Path
from colorama import Fore, Style, init

init(autoreset=True)

def show_analysis(json_file):
    """Показывает результаты анализа из JSON файла."""

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  РЕЗУЛЬТАТЫ AI-АНАЛИЗА{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")

    # Информация о тендере
    tender_info = data.get('tender_info', {})
    print(f"{Fore.WHITE}📝 Тендер:{Style.RESET_ALL} {tender_info.get('name', 'Н/Д')}")
    print(f"{Fore.WHITE}🏢 Заказчик:{Style.RESET_ALL} {tender_info.get('customer', 'Н/Д')}")

    nmck = tender_info.get('nmck')
    if nmck:
        print(f"{Fore.WHITE}💰 НМЦК:{Style.RESET_ALL} {nmck:,.0f} руб.")

    # Сроки
    deadline_submission = tender_info.get('deadline_submission')
    if deadline_submission:
        print(f"{Fore.WHITE}⏰ Срок подачи заявок:{Style.RESET_ALL} {deadline_submission}")

    deadline_execution = tender_info.get('deadline_execution')
    if deadline_execution:
        print(f"{Fore.WHITE}📅 Срок исполнения:{Style.RESET_ALL} {deadline_execution}")

    # Обеспечения
    guarantee_app = tender_info.get('guarantee_application')
    guarantee_contract = tender_info.get('guarantee_contract')

    if guarantee_app:
        if isinstance(guarantee_app, float) and guarantee_app < 1:
            print(f"{Fore.WHITE}💵 Обеспечение заявки:{Style.RESET_ALL} {guarantee_app*100}%")
        else:
            print(f"{Fore.WHITE}💵 Обеспечение заявки:{Style.RESET_ALL} {guarantee_app:,.0f} руб.")

    if guarantee_contract:
        if isinstance(guarantee_contract, float) and guarantee_contract < 1:
            print(f"{Fore.WHITE}💵 Обеспечение контракта:{Style.RESET_ALL} {guarantee_contract*100}%")
        else:
            print(f"{Fore.WHITE}💵 Обеспечение контракта:{Style.RESET_ALL} {guarantee_contract:,.0f} руб.")

    # Товары/услуги
    products = tender_info.get('products_or_services', [])
    if products:
        print(f"\n{Fore.CYAN}📦 ТОВАРЫ/УСЛУГИ:{Style.RESET_ALL}\n")
        for i, product in enumerate(products, 1):
            print(f"  {i}. {product.get('name', 'Н/Д')}")
            print(f"     Количество: {product.get('quantity', 'Н/Д')} {product.get('unit', '')}")
            specs = product.get('specifications', {})
            if specs:
                print(f"     Характеристики:")
                for key, value in specs.items():
                    print(f"       - {key}: {value}")

    # Требования
    requirements = data.get('requirements', {})
    if requirements:
        print(f"\n{Fore.CYAN}📋 ТРЕБОВАНИЯ:{Style.RESET_ALL}\n")
        for req_type, req_list in requirements.items():
            if req_list:
                print(f"  {req_type.upper()}:")
                for req in req_list:
                    print(f"    - {req}")

    # Пробелы в информации
    gaps = data.get('gaps', [])
    if gaps:
        print(f"\n{Fore.YELLOW}⚠️  ОБНАРУЖЕНЫ ПРОБЕЛЫ В ДОКУМЕНТАЦИИ: {len(gaps)}{Style.RESET_ALL}\n")

        critical = [g for g in gaps if g.get('criticality') == 'CRITICAL']
        high = [g for g in gaps if g.get('criticality') == 'HIGH']

        if critical:
            print(f"{Fore.RED}  🔴 КРИТИЧЕСКИЕ ({len(critical)}):{Style.RESET_ALL}")
            for gap in critical[:3]:
                print(f"    - {gap.get('issue', 'Н/Д')}")

        if high:
            print(f"\n{Fore.YELLOW}  🟡 ВЫСОКИЕ ({len(high)}):{Style.RESET_ALL}")
            for gap in high[:3]:
                print(f"    - {gap.get('issue', 'Н/Д')}")

    # Вопросы для заказчика
    questions = data.get('questions', {})
    if questions:
        critical_q = questions.get('critical', [])
        important_q = questions.get('important', [])

        if critical_q or important_q:
            print(f"\n{Fore.CYAN}❓ ВОПРОСЫ ДЛЯ ЗАКАЗЧИКА:{Style.RESET_ALL}\n")

            if critical_q:
                print(f"  {Fore.RED}КРИТИЧЕСКИЕ:{Style.RESET_ALL}")
                for q in critical_q[:3]:
                    print(f"    - {q}")

            if important_q:
                print(f"\n  {Fore.YELLOW}ВАЖНЫЕ:{Style.RESET_ALL}")
                for q in important_q[:3]:
                    print(f"    - {q}")

    # Контакты
    contacts = data.get('contacts', {})
    if contacts:
        print(f"\n{Fore.CYAN}📞 КОНТАКТЫ:{Style.RESET_ALL}\n")
        for key, value in contacts.items():
            if value:
                print(f"  {key}: {value}")

    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")


def main():
    """Главная функция."""

    # Ищем последние JSON файлы с результатами
    reports_dir = Path(__file__).parent / 'output' / 'reports'
    json_files = sorted(reports_dir.glob('tender_report_*.json'), key=lambda x: x.stat().st_mtime, reverse=True)

    if not json_files:
        print("❌ Не найдено JSON файлов с результатами анализа")
        return

    print(f"\n{Fore.CYAN}Найдено {len(json_files)} файлов с результатами анализа{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Показываю последние 3:{Style.RESET_ALL}")

    for json_file in json_files[:3]:
        show_analysis(json_file)
        input(f"{Fore.CYAN}Нажмите Enter для продолжения...{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
