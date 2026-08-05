#!/usr/bin/env python3
"""
Скрипт для автоматического обновления webapp/server.py
Добавляет маршруты для AI мини-приложения
"""

import os
import sys

def update_server():
    server_path = "webapp/server.py"
    
    if not os.path.exists(server_path):
        print("❌ webapp/server.py не найден!")
        return False
    
    with open(server_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Проверяем есть ли уже маршрут /ai
    if '@routes.get("/ai")' in content:
        print("✓ Маршрут /ai уже добавлен в server.py")
        return True
    
    # Ищем где добавить маршрут
    marker = '@routes.get("/")\nasync def index(request):'
    
    if marker not in content:
        print("⚠️  Не удалось найти маршрут index в server.py")
        print("Добавьте вручную эти строки после функции index():")
        print("""
@routes.get("/ai")
async def ai_miniapp(request):
    return web.FileResponse(BASE_DIR / "static" / "ai_miniapp.html")
""")
        return False
    
    # Добавляем новый маршрут
    new_route = '''@routes.get("/")
async def index(request):
    return web.FileResponse(BASE_DIR / "static" / "index.html")

# ====================== AI МиниПриложение ======================
@routes.get("/ai")
async def ai_miniapp(request):
    """Serve the AI mini app interface"""
    return web.FileResponse(BASE_DIR / "static" / "ai_miniapp.html")
'''
    
    content = content.replace(marker.replace('\n', '\n    '), new_route)
    
    # Проверяем есть ли импорт routes_ai_miniapp
    if 'from webapp.routes_ai_miniapp import routes as ai_routes' not in content:
        # Ищем функцию create_app
        create_app_marker = 'def create_app():\n    app = web.Application(middlewares=[error_middleware])\n    app.add_routes(routes)'
        
        if create_app_marker in content:
            new_create_app = '''def create_app():
    app = web.Application(middlewares=[error_middleware])
    app.add_routes(routes)
    
    # Добавляем маршруты для AI мини-приложения
    from webapp.routes_ai_miniapp import routes as ai_routes
    app.add_routes(ai_routes)'''
            
            content = content.replace(create_app_marker, new_create_app)
    
    # Сохраняем файл
    with open(server_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✅ webapp/server.py обновлен успешно!")
    return True

if __name__ == "__main__":
    print("🔧 Обновление server.py для AI мини-приложения...\n")
    
    if update_server():
        print("\n✨ Готово!")
        print("\nДальше:")
        print("  1. Запустите: python main.py")
        print("  2. Откройте: http://localhost:8080/ai")
        print("  3. Установите в BotFather: https://your-domain.com/ai")
    else:
        print("\n❌ Не удалось обновить server.py автоматически")
        print("Добавьте маршруты вручную (см. выше)")
        sys.exit(1)
