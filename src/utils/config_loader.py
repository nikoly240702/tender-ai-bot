"""
Модуль для загрузки конфигурационных файлов.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv


class ConfigLoader:
    """Класс для загрузки и управления конфигурацией приложения."""

    def __init__(self, config_dir: Optional[str] = None):
        """
        Инициализация загрузчика конфигурации.

        Args:
            config_dir: Путь к директории с конфигурационными файлами.
                       Если None, используется относительный путь от корня проекта.
        """
        if config_dir is None:
            # Определяем корневую директорию проекта (на 2 уровня выше от utils)
            self.config_dir = Path(__file__).parent.parent.parent / 'config'
        else:
            self.config_dir = Path(config_dir)

        # Загружаем переменные окружения из .env файла
        env_path = self.config_dir.parent / '.env'
        if env_path.exists():
            load_dotenv(env_path)

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        """
        Загружает YAML файл из директории конфигурации.

        Args:
            filename: Имя файла (например, 'settings.yaml')

        Returns:
            Словарь с данными из YAML файла

        Raises:
            FileNotFoundError: Если файл не найден
            yaml.YAMLError: При ошибке парсинга YAML
        """
        file_path = self.config_dir / filename

        if not file_path.exists():
            raise FileNotFoundError(
                f"Конфигурационный файл не найден: {file_path}\n"
                f"Убедитесь, что файл существует в директории: {self.config_dir}"
            )

        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
                return config or {}
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Ошибка парсинга YAML файла {filename}: {str(e)}")

    def load_company_profile(self) -> Dict[str, Any]:
        """
        Загружает профиль компании.

        Returns:
            Словарь с данными профиля компании
        """
        return self.load_yaml('company_profile.yaml')

    def load_settings(self) -> Dict[str, Any]:
        """
        Загружает системные настройки.

        Returns:
            Словарь с системными настройками
        """
        return self.load_yaml('settings.yaml')

    def get_api_key(self, key_name: str = None, provider: str = None) -> str:
        """
        Получает API ключ из переменных окружения.

        Args:
            key_name: Имя переменной окружения с API ключом
            provider: Название провайдера (автоматически определяет имя ключа)

        Returns:
            API ключ

        Raises:
            ValueError: Если API ключ не найден
        """
        # Определяем имя ключа по провайдеру
        if provider and not key_name:
            provider_keys = {
                'anthropic': 'ANTHROPIC_API_KEY',
                'openai': 'OPENAI_API_KEY',
                'groq': 'GROQ_API_KEY',
                'gemini': 'GOOGLE_API_KEY',
                'ollama': None  # Не требует ключа
            }
            key_name = provider_keys.get(provider.lower())
            if key_name is None and provider.lower() == 'ollama':
                return None  # Ollama не требует ключа

        if not key_name:
            key_name = 'ANTHROPIC_API_KEY'  # По умолчанию

        api_key = os.getenv(key_name)

        if not api_key:
            raise ValueError(
                f"API ключ '{key_name}' не найден в переменных окружения.\n"
                f"Убедитесь, что:\n"
                f"1. Создан файл .env в корне проекта\n"
                f"2. В файле указан {key_name}=your-key-here\n"
                f"3. Или установлена переменная окружения: export {key_name}=your-key-here"
            )

        return api_key

    def get_llm_config(self) -> Dict[str, Any]:
        """
        Получает конфигурацию для LLM API с учетом API ключа.

        Returns:
            Словарь с настройками LLM API:
            {
                'provider': str,
                'api_key': str,
                'model': str,
                'max_tokens': int,
                'temperature': float,
                ...
            }
        """
        settings = self.load_settings()
        llm_config = settings.get('llm', {})

        # Получаем провайдера
        provider = llm_config.get('provider', 'openai')

        # Добавляем API ключ (если требуется)
        api_key = self.get_api_key(provider=provider)
        if api_key:
            llm_config['api_key'] = api_key

        llm_config['provider'] = provider

        return llm_config

    def get_claude_config(self) -> Dict[str, Any]:
        """
        Получает конфигурацию для Claude API с учетом API ключа.
        (Для обратной совместимости)

        Returns:
            Словарь с настройками Claude API
        """
        settings = self.load_settings()
        claude_config = settings.get('claude', {})

        # Добавляем API ключ
        try:
            claude_config['api_key'] = self.get_api_key('ANTHROPIC_API_KEY')
        except ValueError:
            # Если ключа нет, используем конфигурацию LLM
            return self.get_llm_config()

        return claude_config

    def get_paths(self) -> Dict[str, Path]:
        """
        Получает абсолютные пути к директориям проекта.

        Returns:
            Словарь с путями:
            {
                'root': Path,
                'data': Path,
                'output': Path,
                'prompts': Path,
                'config': Path
            }
        """
        root_dir = self.config_dir.parent
        settings = self.load_settings()
        paths_config = settings.get('paths', {})

        paths = {
            'root': root_dir,
            'data': root_dir / paths_config.get('data_folder', 'data/tenders'),
            'output': root_dir / paths_config.get('output_folder', 'output/reports'),
            'prompts': root_dir / paths_config.get('prompts_folder', 'prompts'),
            'config': self.config_dir
        }

        # Создаем директории, если их нет
        for key, path in paths.items():
            if key != 'root' and key != 'config':
                path.mkdir(parents=True, exist_ok=True)

        return paths

    def get_value(self, *keys, config_type: str = 'settings', default: Any = None) -> Any:
        """
        Получает значение из конфигурации по цепочке ключей.

        Args:
            *keys: Последовательность ключей для доступа к вложенным значениям
            config_type: Тип конфигурации ('settings' или 'company_profile')
            default: Значение по умолчанию, если ключ не найден

        Returns:
            Значение из конфигурации или default

        Example:
            >>> loader = ConfigLoader()
            >>> loader.get_value('claude', 'model')
            'claude-sonnet-4-20250514'
        """
        if config_type == 'settings':
            config = self.load_settings()
        elif config_type == 'company_profile':
            config = self.load_company_profile()
        else:
            raise ValueError(f"Неизвестный тип конфигурации: {config_type}")

        # Проходим по цепочке ключей
        value = config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value


def main():
    """Пример использования ConfigLoader."""
    loader = ConfigLoader()

    print("=== Системные настройки ===")
    settings = loader.load_settings()
    print(f"Модель Claude: {settings['claude']['model']}")
    print(f"Max tokens: {settings['claude']['max_tokens']}")

    print("\n=== Профиль компании ===")
    company = loader.load_company_profile()
    print(f"Компания: {company['company_info']['name']}")
    print(f"Компетенции: {len(company['capabilities']['categories'])}")

    print("\n=== Пути ===")
    paths = loader.get_paths()
    for name, path in paths.items():
        print(f"{name}: {path}")

    print("\n=== Проверка API ключа ===")
    try:
        api_key = loader.get_api_key()
        print(f"API ключ найден: {api_key[:10]}...")
    except ValueError as e:
        print(f"Ошибка: {e}")


if __name__ == "__main__":
    main()
