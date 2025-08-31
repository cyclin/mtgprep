# Executive Meeting Brief Generator

A FastAPI application that generates executive meeting briefs for both internal meetings and external business development opportunities. Combines Slack conversation context with HubSpot contact information for internal meetings, and web research intelligence for BD meetings, using OpenAI's o3 model for intelligent analysis.

## Features

### Internal Meeting Prep (`/`)
- **Slack Integration**: Fetches recent messages from specified channels with thread expansion
- **HubSpot Integration**: Enriches attendee information with CRM data
- **AI-Powered Analysis**: Uses OpenAI's o3 model to generate comprehensive meeting briefs
- **Tactical Focus**: Optimized for internal team coordination and account management

### BD Meeting Intelligence (`/bd`)
- **Web Research**: Automated company and executive research using web search
- **Competitive Analysis**: Industry landscape and competitor intelligence gathering
- **Executive Profiling**: Background research on key decision makers
- **Strategic Intelligence**: Comprehensive reports for external business development meetings
- **Document Analysis**: Support for parsing company reports and financial documents

### Shared Features
- **Responsive Web Interface**: Clean, modern UI following CroMetrics design standards
- **Flexible AI Reasoning**: Configurable effort levels for different meeting importance
- **Export-Ready Reports**: Markdown-formatted outputs for easy sharing

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
   HUBSPOT_TOKEN=your_hubspot_private_app_token  # Optional, for internal meeting attendee enrichment
   SERPER_API_KEY=your_serper_api_key  # Optional, for BD meeting web research capabilities
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

### Internal Meeting Prep
1. Open your browser to `http://localhost:3000`
2. Select a Slack channel from the dropdown
3. Configure message limits, lookback days, and reasoning effort
4. Optionally add attendee emails for HubSpot enrichment
5. Specify the meeting purpose
6. Click "Run" to generate your executive brief

### BD Meeting Intelligence
1. Navigate to `http://localhost:3000/bd`
2. Enter target company name and key executive details
3. Optionally specify industry context
4. Describe meeting objectives and context
5. Select research depth (Quick/Standard/Comprehensive)
6. Click "Generate Intelligence Report" for strategic analysis

**Note**: BD functionality requires `SERPER_API_KEY` for web research. Without it, reports will indicate research is unavailable.

## API Endpoints

### Internal Meeting Endpoints
- `GET /` - Internal meeting web interface
- `GET /api/channels` - List available Slack channels
- `POST /api/run` - Generate internal meeting brief

### BD Meeting Endpoints  
- `GET /bd` - BD meeting intelligence web interface
- `POST /api/bd/generate` - Generate BD intelligence report

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
- Slack workspace with appropriate bot/user token (for internal meetings)
- HubSpot Private App token (optional, for internal meeting attendee enrichment)
- Serper API key (optional, for BD meeting web research capabilities)
