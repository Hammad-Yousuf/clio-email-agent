# Clio Matter Mapping Agent

Azure Functions-based agent that classifies inbound Outlook emails to the correct Clio Manage Matter and writes back as Communications or Draft Notes.

## Features

- **Email Classification**: Matches emails to matters using multiple signals (matter number, client name, keywords)
- **Confidence Scoring**: Weighted scoring algorithm with configurable threshold
- **Automatic Writeback**: High confidence → Communication, Low confidence → Draft Note to review queue
- **Audit Logging**: All operations logged to Azure Table Storage
- **Matter Caching**: Local cache for improved performance

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/classify` | POST | Classification only - returns matter match with confidence |
| `/api/classify-and-writeback` | POST | Classify + auto writeback (high confidence → matter, low confidence → review queue) |
| `/api/writeback` | POST | Force communication writeback to specified matter |
| `/api/matters` | GET | Cached matters snapshot with `?refresh=true` support |
| `/api/health` | GET | Health check with component status |

## Local Development

### Prerequisites

- Python 3.9+
- Azure Functions Core Tools
- Clio Manage API token

### Setup

1. Install Azure Functions Core Tools:
   ```bash
   npm install -g azure-functions-core-tools@4
   ```

2. Copy and configure settings:
   ```bash
   cp local.settings.json.example local.settings.json
   # Edit local.settings.json with your values
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run locally:
   ```bash
   func start
   ```

### Configuration

Edit `local.settings.json`:

```json
{
  "Values": {
    "CLIO_API_TOKEN": "your_clio_api_token_here",
    "CLIO_API_BASE_URL": "https://app.clio.com",
    "CLIO_API_VERSION": "v4",
    "AZURE_STORAGE_CONNECTION_STRING": "your_storage_connection_string",
    "REVIEW_QUEUE_MATTER_ID": "12345",
    "CONFIDENCE_THRESHOLD": "0.7",
    "CACHE_DIR": ".local_cache"
  }
}
```

### Test the API

```bash
# Health check
curl http://localhost:7071/api/health

# Get matters
curl http://localhost:7071/api/matters

# Classify an email
curl -X POST http://localhost:7071/api/classify \
  -H "Content-Type: application/json" \
  -d '{
    "email_id": "test-123",
    "subject": "Re: 10001 - Alice Alpha Injury Claim Update",
    "body": "Please find attached the medical records for Alice Alpha case.",
    "sender_email": "alice.alpha@email.com",
    "sender_name": "Alice Alpha",
    "received_at": "2026-03-07T10:00:00Z"
  }'

# Classify and writeback
curl -X POST http://localhost:7071/api/classify-and-writeback \
  -H "Content-Type: application/json" \
  -d '{
    "email_id": "test-456",
    "subject": "Meeting notes",
    "body": "Discussion about the case.",
    "sender_email": "client@example.com",
    "sender_name": "John Client",
    "received_at": "2026-03-07T11:00:00Z"
  }'

# Force writeback
curl -X POST http://localhost:7071/api/writeback \
  -H "Content-Type: application/json" \
  -d '{
    "email_id": "test-789",
    "matter_id": "1",
    "subject": "Important Update",
    "body": "This is the email body.",
    "sender_email": "sender@example.com",
    "received_at": "2026-03-07T12:00:00Z"
  }'
```

## Deployment

### Azure Portal

1. Create a new Function App in Azure Portal
2. Configure Application Settings with the same values as `local.settings.json`
3. Deploy using Azure Functions Core Tools:
   ```bash
   func azure functionapp publish <your-function-app-name>
   ```

### CI/CD

Use Azure DevOps or GitHub Actions with the Azure Functions Action:

```yaml
- uses: Azure/functions-action@v1
  with:
    app-name: 'your-function-app-name'
    package: './azure_functions'
```

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Outlook Email │────▶│  Azure Function  │────▶│  Classification │
│   (Inbound)     │     │  /api/classify   │     │  Engine         │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                              ┌───────────────────────────┼───────────┐
                              │                           │           │
                              ▼                           ▼           ▼
                    ┌─────────────────┐        ┌─────────────────┐  ┌─────────────────┐
                    │ High Confidence │        │ Low Confidence  │  │  Audit Logger   │
                    │ (≥ threshold)   │        │ (< threshold)   │  │  (Azure Table)  │
                    └────────┬────────┘        └────────┬────────┘  └─────────────────┘
                             │                          │
                             ▼                          ▼
                    ┌─────────────────┐        ┌─────────────────┐
                    │  Communication  │        │  Draft Note     │
                    │  (Target Matter)│        │  (Review Queue) │
                    └─────────────────┘        └─────────────────┘
```

## Classification Scoring

The classifier uses weighted signals:

| Signal | Weight | Description |
|--------|--------|-------------|
| Matter Number Match | 0.40 | Exact match of matter number in subject/body |
| Client Name Match | 0.30 | Client name found in sender/from fields |
| Matter Type Keywords | 0.15 | Matter type keywords in email content |
| Matter Name Keywords | 0.15 | Matter name keywords in email content |

**Threshold**: Default is 0.7 (70%)
- Score ≥ 0.7 → Create Communication in matched matter
- Score < 0.7 → Create Draft Note in review queue matter

## License

MIT
