# scripts/clear_cache.py
from app.services.cache_service import cache_service

cache_service.clear()
print("Cache sémantique vidé.")