# ATR Report & Tinkoff API

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Uvicorn](https://img.shields.io/badge/Uvicorn-499848?style=flat&logo=gunicorn)](https://www.uvicorn.org/)

Бэкенд-сервис на базе FastAPI для генерации отчетов ATR (Московская Биржа) и работы с API Т-Инвестиций.

## Основные возможности
* Генерация ATR-отчетов и отдача HTML-страниц.
* Интеграция с публичным API MOEX ISS (не требует ключей).
* Интеграция с T-Invest Python SDK для работы с биржевыми данными.
* Работа за Nginx (проксирование на порт 8001).

## Требования
* **Python 3.12** (строго! На старых версиях не установятся современные `pandas` и `numpy`).
* Linux (Ubuntu / Debian).

## Установка

1. Клонируйте репозиторий и создайте виртуальное окружение:
   `python3.12 -m venv venv`
   `source venv/bin/activate`

2. Установите основные зависимости:
   `pip install --upgrade pip`
   `pip install -r requirements.txt`

3. Установите T-Invest SDK (временно недоступен в PyPI, ставится из официального wheel-файла):
   `pip install "https://github.com/Invest-SDK/invest-python/raw/refs/heads/master/t_tech_investments-1.51.0-py3-none-any.whl"`

*(Также вы можете воспользоваться готовым скриптом `./install.sh`)*

## Переменные окружения
Для работы приложения требуется файл с секретами. По умолчанию systemd-служба ожидает его по пути:
`/etc/moex-atr.env`

В файле должен быть указан токен Т-Инвестиций:
`TINKOFF_TOKEN=t.ваш_токен_здесь`

## Запуск
Для локального запуска:
`uvicorn main:app --host 127.0.0.1 --port 8001`

Для работы в продакшене:
`sudo systemctl start atr-report.service`
`sudo systemctl enable atr-report.service`

## Маршруты (Endpoints)
Согласно конфигурации Nginx, сервис доступен по следующим путям:
* `/atr.html` — интерфейс отчета ATR
* `/tinkoff.html` — интерфейс отчета Tinkoff
* `/api/atr/` — REST API для ATR
* `/api/tinkoff/` — REST API для Tinkoff

## Лицензия
Проект создан для внутреннего использования.
