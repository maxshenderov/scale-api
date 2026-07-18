FROM python:3.12-slim

WORKDIR /app

ENV PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"
ENV PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
