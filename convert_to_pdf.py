#!/usr/bin/env python3
"""
Скрипт для конвертации отчета markdown в PDF
"""

import sys
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import re


def register_fonts():
    """Регистрируем поддержку русского языка"""
    try:
        # Попробуем зарегистрировать системные шрифты для macOS
        pdfmetrics.registerFont(TTFont('DejaVuSans', '/System/Library/Fonts/Supplemental/Arial.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', '/System/Library/Fonts/Supplemental/Arial Bold.ttf'))
        return 'DejaVuSans'
    except:
        # Если не получилось, используем Helvetica (ограниченная поддержка кириллицы)
        return 'Helvetica'


def parse_markdown(md_file):
    """Простой парсер markdown для извлечения структурированных данных"""
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()

    data = {
        'title': 'Отчет по анализу тендера',
        'sections': []
    }

    lines = content.split('\n')
    current_section = None
    current_text = []

    for line in lines:
        if line.startswith('# '):
            data['title'] = line[2:].strip()
        elif line.startswith('## '):
            if current_section:
                current_section['content'] = '\n'.join(current_text)
                data['sections'].append(current_section)
            current_section = {'title': line[3:].strip(), 'content': ''}
            current_text = []
        elif current_section:
            current_text.append(line)

    if current_section:
        current_section['content'] = '\n'.join(current_text)
        data['sections'].append(current_section)

    return data


def create_pdf(md_file, pdf_file):
    """Создает PDF из markdown файла"""

    # Регистрируем шрифты
    font_name = register_fonts()

    # Создаем PDF
    doc = SimpleDocTemplate(
        pdf_file,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )

    # Создаем стили
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=24,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=30,
        alignment=TA_CENTER
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontName=font_name + '-Bold' if font_name != 'Helvetica' else font_name,
        fontSize=16,
        textColor=colors.HexColor('#34495e'),
        spaceBefore=12,
        spaceAfter=6
    )

    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=11,
        leading=14
    )

    # Парсим markdown
    data = parse_markdown(md_file)

    # Создаем элементы документа
    story = []

    # Заголовок
    story.append(Paragraph(data['title'], title_style))
    story.append(Spacer(1, 0.5*cm))

    # Секции
    for section in data['sections']:
        story.append(Paragraph(section['title'], heading_style))
        story.append(Spacer(1, 0.3*cm))

        # Обрабатываем содержимое секции
        content_lines = section['content'].strip().split('\n')
        for line in content_lines:
            if line.strip():
                # Убираем markdown форматирование
                line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                line = re.sub(r'\*(.*?)\*', r'<i>\1</i>', line)
                line = re.sub(r'`(.*?)`', r'<font face="Courier">\1</font>', line)

                # Обрабатываем списки
                if line.strip().startswith('- '):
                    line = '• ' + line.strip()[2:]
                elif re.match(r'^\d+\. ', line.strip()):
                    pass  # Нумерованные списки оставляем как есть

                try:
                    story.append(Paragraph(line, normal_style))
                except:
                    # Если не получилось создать параграф (проблемы с кодировкой)
                    # используем простой текст
                    story.append(Paragraph(line.encode('ascii', 'ignore').decode(), normal_style))

                story.append(Spacer(1, 0.1*cm))

        story.append(Spacer(1, 0.3*cm))

    # Генерируем PDF
    doc.build(story)
    print(f'✓ PDF создан: {pdf_file}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Использование: python convert_to_pdf.py <markdown_file>')
        sys.exit(1)

    md_file = sys.argv[1]
    if not os.path.exists(md_file):
        print(f'Ошибка: файл {md_file} не найден')
        sys.exit(1)

    pdf_file = md_file.replace('.md', '.pdf')
    create_pdf(md_file, pdf_file)
