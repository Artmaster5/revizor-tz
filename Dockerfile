# Ревизор ТЗ как сайт для пробы (Render.com и любой хостинг с Docker). Шаги — docs/DEPLOY.md.
# Ключ модели в образ не попадает: его задают переменной окружения GEMINI_API_KEY в панели хостинга.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    REVIZOR_NO_DOTENV=1 \
    PORT=10000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 revizor
USER revizor

EXPOSE 10000
CMD ["python", "main.py", "--public"]
