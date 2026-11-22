# Sentiment Analysis Dashboard

## Run the Flask API locally

Create an empty PostgreSQL database and run the following from the repository root in PowerShell. Set `DATABASE_URL` to your connection string. Set the environment variables in the shell where you run Flask.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r backend/requirements.txt
$env:DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@localhost:5432/sentiment_dashboard'
$env:SECRET_KEY = .\venv\Scripts\python.exe -c 'import secrets; print(secrets.token_hex(32))'
$env:SESSION_COOKIE_SECURE = '0'
.\venv\Scripts\python.exe -m flask --app backend/app init-db
.\venv\Scripts\python.exe -m flask --app backend/app run
```

For HTTPS deployments, keep `SESSION_COOKIE_SECURE` enabled and configure a stable, randomly generated `SECRET_KEY` shared by the API processes.

If you initialized your database before user accounts were added, follow the [existing database upgrade](docs/data-model.md). See the [API guide](docs/api.md) for registration, sign-in, and dataset creation.

To upload feedback from a UTF-8 CSV, follow the [CSV import example](docs/api.md#import-feedback-from-csv). Imports use the existing tables and require Flask 3.1 or newer.

## Run the API integration tests

Set `TEST_DATABASE_URL` to a PostgreSQL test database. The test role needs permission to create schemas. The suite creates and removes its own schema in that database.

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s backend/tests -v
```
