# Accounts and dataset access

| Method and path | Request | Success |
| --- | --- | --- |
| `POST /api/auth/register` | JSON `email` and `password` | `201` with `id` and `email` |
| `POST /api/auth/login` | JSON `email` and `password` | `200` with `id` and `email`, plus a signed session cookie |
| `GET /api/me` | Session cookie | `200` with the current user's `id` and `email` |
| `DELETE /api/auth/session` | Session cookie | `204` and clears the cookie |

Email addresses are trimmed and lowercased. Passwords must be 12–128 characters. Registration returns `409` for a duplicate email. Sign in after registering to receive a session cookie. For a browser frontend, serve the API on the same origin so the browser sends the cookie with requests.

All dataset, sample, topic, and results endpoints require a session. Dataset creation assigns ownership from the session and the dataset list returns only the caller's datasets. A missing session returns `401`. Requesting another user's dataset or anything inside it returns `404`.

## Try it in PowerShell

```powershell
$api = 'http://127.0.0.1:5000/api'
$credentials = @{ email = 'owner@example.test'; password = 'Local-test-password-12' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/auth/register" -ContentType 'application/json' -Body $credentials
Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType 'application/json' -Body $credentials -SessionVariable apiSession
Invoke-RestMethod -Uri "$api/me" -WebSession $apiSession
Invoke-RestMethod -Method Post -Uri "$api/datasets" -ContentType 'application/json' -Body '{"name":"November feedback"}' -WebSession $apiSession
Invoke-RestMethod -Method Delete -Uri "$api/auth/session" -WebSession $apiSession
```

## Import feedback from CSV

Send `POST /api/datasets/{dataset_id}/samples/import` with a session cookie and a `multipart/form-data` field named `file`. The file must be a UTF-8, comma-separated CSV. A UTF-8 BOM is supported. Its header must be `text` or `text,source_date`, with either column order accepted when both are present. Quote text containing commas or line breaks.

```csv
text,source_date
"Delivery was fast, and the driver was helpful.",2026-11-22
"The packaging was damaged.",
```

Dates use `YYYY-MM-DD`. An empty date is stored as `NULL`. Empty physical lines are ignored, while a row with blank text is rejected. Imports allow up to 5 MiB per file and 10,000 data rows, with a 6 MiB total HTTP request limit for multipart framing. Parsing errors, invalid rows, or a limit violation reject the whole import. Successful uploads append samples and return `201` with `dataset_id` and `created_count`. Uploading the same file again creates another copy of its rows.

In PowerShell 7, after signing in and creating a dataset, save a CSV and upload it with the same session. Replace `1` with the dataset ID returned by the API.

```powershell
@'
text,source_date
"Delivery was fast.",2026-11-22
"The packaging was damaged.",
'@ | Set-Content -Path feedback.csv -Encoding utf8

Invoke-RestMethod -Method Post -Uri "$api/datasets/1/samples/import" -Form @{ file = Get-Item feedback.csv } -WebSession $apiSession
Invoke-RestMethod -Uri "$api/datasets/1/samples" -WebSession $apiSession | ConvertTo-Json -Depth 5
```

The rows are stored in `text_samples`, using the existing schema. You can inspect them in pgAdmin with `SELECT * FROM public.text_samples ORDER BY id;`.
