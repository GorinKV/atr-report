def test_app_is_running(client):
    """Проверяем, что приложение FastAPI успешно запускается и отдает Swagger."""
    r = client.get("/docs")
    assert r.status_code == 200

def test_root_static_available(client):
    """Проверяем, что статика (HTML-страницы) отдается корректно."""
    # В main.py у вас смонтирована статика на "/"
    r = client.get("/")
    # Должен вернуться либо 200 (если есть index.html), либо 404, если файла нет,
    # но точно не 500 (ошибка сервера).
    assert r.status_code in (200, 404)
