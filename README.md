# Executive Meeting Brief Generator

A FastAPI application that generates executive meeting briefs by combining Slack conversation context with HubSpot contact information and using OpenAI's o3 model for intelligent analysis.

## Features

- **Slack Integration**: Fetches recent messages from specified channels with thread expansion
- **HubSpot Integration**: Enriches attendee information with CRM data
- **AI-Powered Analysis**: Uses OpenAI's o3 model to generate comprehensive meeting briefs
- **Web Interface**: Simple, responsive UI for configuring and running brief generation

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Variables**:
   Create a `.env` file with the following variables:
   ```
   OPENAI_API_KEY=your_openai_api_key
   SLACK_TOKEN=your_slack_token  # Can be SLACK_USER_TOKEN, SLACK_BOT_TOKEN, or SLACK_TOKEN
   HUBSPOT_TOKEN=your_hubspot_private_app_token  # Optional
   PORT=3000  # Optional, defaults to 3000
   ```

3. **Run the Application**:
   ```bash
   python app.py
   ```
   
   Or with uvicorn directly:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 3000 --reload
   ```

## Usage

1. Open your browser to `http://localhost:3000`
2. Select a Slack channel from the dropdown
3. Configure message limits, lookback days, and reasoning effort
4. Optionally add attendee emails for HubSpot enrichment
5. Specify the meeting purpose
6. Click "Run" to generate your executive brief

## API Endpoints

- `GET /` - Web interface
- `GET /api/channels` - List available Slack channels
- `POST /api/run` - Generate meeting brief

## Deployment

This application is designed to be deployed on platforms like Railway, Render, or any Python hosting service that supports FastAPI applications.

### Railway Deployment
1. Connect your GitHub repository to Railway
2. Set the environment variables in Railway's dashboard
3. Railway will automatically detect and deploy your FastAPI app

### Render Deployment
1. Connect your GitHub repository to Render
2. Create a new Web Service
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `python app.py`
5. Add environment variables in Render's dashboard

## Requirements

- Python 3.8+
- OpenAI API key with o3 model access
- Slack workspace with appropriate bot/user token
- HubSpot Private App token (optional, for attendee enrichment)
