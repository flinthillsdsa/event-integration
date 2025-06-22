#!/usr/bin/env python3
"""
Action Network to TeamUp Integration Service
Receives webhooks from Action Network and creates events in TeamUp Calendar
"""

from flask import Flask, request, jsonify
import requests
import json
import os
from datetime import datetime, timezone
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration - set these as environment variables
TEAMUP_API_KEY = os.environ.get('TEAMUP_API_KEY')
TEAMUP_CALENDAR_KEY = os.environ.get('TEAMUP_CALENDAR_KEY')

class TeamUpIntegrator:
    def __init__(self):
        self.teamup_base_url = f"https://api.teamup.com/{TEAMUP_CALENDAR_KEY}"
        self.teamup_headers = {
            'Teamup-Token': TEAMUP_API_KEY,
            'Content-Type': 'application/json'
        }
    
    def transform_event_data(self, action_network_data):
        """
        Transform Action Network event data to TeamUp format
        """
        event = action_network_data.get('event', {})
        
        # Extract event details
        title = event.get('title', 'Untitled Event')
        description = event.get('description', '')
        start_date = event.get('start_date')
        end_date = event.get('end_date', start_date)  # Use start_date if no end_date
        location = event.get('location', {})
        
        # Format location
        location_str = self.format_location(location)
        
        # TeamUp event format
        teamup_event = {
            'subcalendar_ids': [14502152],  # Default to Committee Meetings
            'title': title,
            'notes': description,
            'location': location_str,
            'start_dt': start_date,
            'end_dt': end_date,
            'all_day': False
        }
        
        # Remove empty fields
        teamup_event = {k: v for k, v in teamup_event.items() if v}
        
        return teamup_event
    
    def format_location(self, location):
        """
        Format location data from Action Network to a string
        """
        if not location:
            return ""
        
        location_str = ""
        if isinstance(location, dict):
            # Handle Action Network location object
            address_lines = location.get('address_lines', [])
            locality = location.get('locality', '')
            region = location.get('region', '')
            postal_code = location.get('postal_code', '')
            country = location.get('country', '')
            
            parts = []
            if address_lines:
                parts.extend(address_lines)
            if locality:
                parts.append(locality)
            if region:
                parts.append(region)
            if postal_code:
                parts.append(postal_code)
            if country and country.upper() != 'US':  # Only add country if not US
                parts.append(country)
            
            location_str = ', '.join(parts)
        elif isinstance(location, str):
            location_str = location
        
        return location_str
    
    def create_teamup_event(self, event_data):
        """
        Create an event in TeamUp Calendar
        """
        try:
            url = f"{self.teamup_base_url}/events"
            
            logger.info(f"Creating TeamUp event: {event_data.get('title', 'No title')}")
            logger.info(f"TeamUp API URL: {url}")
            logger.info(f"Event data: {json.dumps(event_data, indent=2)}")
            
            response = requests.post(url, headers=self.teamup_headers, json=event_data)
            
            logger.info(f"TeamUp API response status: {response.status_code}")
            logger.info(f"TeamUp API response: {response.text}")
            
            if response.status_code == 201:
                logger.info(f"✅ Successfully created TeamUp event: {event_data['title']}")
                return response.json()
            else:
                logger.error(f"❌ Failed to create TeamUp event: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error creating TeamUp event: {str(e)}")
            return None
    
    def test_teamup_connection(self):
        """
        Test the connection to TeamUp API
        """
        try:
            url = f"{self.teamup_base_url}/events"
            
            # Just try to get events to test the connection
            response = requests.get(url, headers=self.teamup_headers, params={'limit': 1})
            
            if response.status_code == 200:
                logger.info("✅ TeamUp API connection successful")
                return True
            else:
                logger.error(f"❌ TeamUp API connection failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error testing TeamUp connection: {str(e)}")
            return False
    
    def process_webhook(self, webhook_data):
        """
        Process the webhook from Action Network
        """
        event_type = webhook_data.get('event_type', '')
        
        if event_type in ['event.created', 'event.updated']:
            logger.info(f"Processing {event_type} webhook")
            
            # Transform and create TeamUp event
            teamup_data = self.transform_event_data(webhook_data)
            teamup_result = self.create_teamup_event(teamup_data)
            
            return {
                'success': teamup_result is not None,
                'teamup_event_id': teamup_result.get('event', {}).get('id') if teamup_result else None,
                'transformed_data': teamup_data
            }
        else:
            logger.info(f"Ignoring webhook type: {event_type}")
            return {'message': f'Webhook type {event_type} not processed'}

# Initialize integrator
integrator = TeamUpIntegrator()

@app.route('/', methods=['GET'])
def home():
    """
    Home page - shows service status
    """
    return jsonify({
        'service': 'Action Network to TeamUp Integration',
        'status': 'running',
        'endpoints': {
            'webhook': '/webhook/action-network',
            'health': '/health',
            'test': '/test'
        }
    })

@app.route('/webhook/action-network', methods=['POST'])
def handle_action_network_webhook():
    """
    Handle incoming webhooks from Action Network
    """
    try:
        data = request.get_json()
        
        logger.info("🔔 Received Action Network webhook")
        logger.info(f"Event type: {data.get('event_type', 'unknown')}")
        logger.info(f"Event title: {data.get('event', {}).get('title', 'No title')}")
        
        # Process the webhook
        result = integrator.process_webhook(data)
        
        return jsonify({
            'status': 'success',
            'message': 'Webhook processed',
            'result': result
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint
    """
    teamup_configured = bool(TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY)
    teamup_connection = False
    
    if teamup_configured:
        teamup_connection = integrator.test_teamup_connection()
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'teamup_api_key_configured': bool(TEAMUP_API_KEY),
            'teamup_calendar_key_configured': bool(TEAMUP_CALENDAR_KEY),
            'teamup_connection_working': teamup_connection
        }
    })

@app.route('/test', methods=['POST'])
def test_integration():
    """
    Test endpoint to verify TeamUp integration works
    """
    test_data = {
        "event_type": "event.created",
        "event": {
            "id": "test-123",
            "title": "🧪 Railway Test Event",
            "description": "This is a test event to verify the Railway deployment is working correctly.",
            "start_date": "2025-07-01T19:00:00Z",
            "end_date": "2025-07-01T21:00:00Z",
            "location": {
                "address_lines": ["123 Test Street"],
                "locality": "Test City",
                "region": "Test State",
                "postal_code": "12345"
            }
        }
    }
    
    logger.info("🧪 Running integration test...")
    result = integrator.process_webhook(test_data)
    
    return jsonify({
        'status': 'test_completed',
        'result': result
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    # Check configuration
    if not TEAMUP_API_KEY:
        logger.warning("⚠️  TeamUp API key not configured!")
    
    if not TEAMUP_CALENDAR_KEY:
        logger.warning("⚠️  TeamUp Calendar key not configured!")
    
    logger.info(f"🚀 Starting TeamUp Integrator on port {port}")
    logger.info(f"📡 Webhook endpoint: /webhook/action-network")
    logger.info(f"❤️  Health check: /health")
    logger.info(f"🧪 Test endpoint: /test")
    
    app.run(host='0.0.0.0', port=port, debug=False)