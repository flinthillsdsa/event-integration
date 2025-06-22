#!/usr/bin/env python3
"""
Action Network to TeamUp Integration Service
Polls Action Network for events and creates them in TeamUp Calendar
"""

from flask import Flask, request, jsonify
import requests
import json
import os
from datetime import datetime, timezone, timedelta
import logging
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration - set these as environment variables
TEAMUP_API_KEY = os.environ.get('TEAMUP_API_KEY')
TEAMUP_CALENDAR_KEY = os.environ.get('TEAMUP_CALENDAR_KEY')
ACTION_NETWORK_API_KEY = os.environ.get('ACTION_NETWORK_API_KEY')
ACTION_NETWORK_ORG = 'fhdsa'  # Your organization slug

# In-memory storage for synced events (in production, you'd use a database)
synced_events = set()

class ActionNetworkTeamUpSync:
    def __init__(self):
        self.teamup_base_url = f"https://api.teamup.com/{TEAMUP_CALENDAR_KEY}"
        self.teamup_headers = {
            'Teamup-Token': TEAMUP_API_KEY,
            'Content-Type': 'application/json'
        }
        self.action_network_headers = {
            'OSDI-API-Token': ACTION_NETWORK_API_KEY,
            'Content-Type': 'application/json'
        }
        self.action_network_base_url = 'https://actionnetwork.org/api/v2'
    
    def fetch_action_network_events(self, limit=25):
        """
        Fetch events from Action Network API
        """
        try:
            url = f"{self.action_network_base_url}/events"
            params = {
                'limit': limit,
                'filter': f'organization_slug eq "{ACTION_NETWORK_ORG}"'
            }
            
            logger.info(f"Fetching events from Action Network...")
            response = requests.get(url, headers=self.action_network_headers, params=params)
            
            if response.status_code == 200:
                data = response.json()
                events = data.get('_embedded', {}).get('osdi:events', [])
                logger.info(f"✅ Fetched {len(events)} events from Action Network")
                return events
            else:
                logger.error(f"❌ Failed to fetch Action Network events: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error fetching Action Network events: {str(e)}")
            return []
    
    def transform_action_network_event(self, an_event):
        """
        Transform Action Network event to TeamUp format
        """
        try:
            title = an_event.get('title', 'Untitled Event')
            description = an_event.get('description', '')
            
            # Handle start/end times
            start_date = None
            end_date = None
            
            # Action Network events can have multiple start/end times
            if 'start_date' in an_event:
                start_date = an_event['start_date']
            elif 'start_time' in an_event:
                start_date = an_event['start_time']
            
            if 'end_date' in an_event:
                end_date = an_event['end_date']
            elif 'end_time' in an_event:
                end_date = an_event['end_time']
            
            # If no end time, assume 2 hours after start
            if start_date and not end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    end_dt = start_dt + timedelta(hours=2)
                    end_date = end_dt.isoformat().replace('+00:00', 'Z')
                except:
                    end_date = start_date
            
            # Handle location
            location_str = ""
            location = an_event.get('location', {})
            if location:
                if isinstance(location, dict):
                    venue = location.get('venue', '')
                    address_lines = location.get('address_lines', [])
                    locality = location.get('locality', '')
                    region = location.get('region', '')
                    postal_code = location.get('postal_code', '')
                    
                    parts = []
                    if venue:
                        parts.append(venue)
                    if address_lines:
                        parts.extend(address_lines)
                    if locality:
                        parts.append(locality)
                    if region:
                        parts.append(region)
                    if postal_code:
                        parts.append(postal_code)
                    
                    location_str = ', '.join(parts)
                elif isinstance(location, str):
                    location_str = location
            
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
            
        except Exception as e:
            logger.error(f"❌ Error transforming event: {str(e)}")
            return None
    
    def create_teamup_event(self, event_data):
        """
        Create an event in TeamUp Calendar
        """
        try:
            url = f"{self.teamup_base_url}/events"
            
            response = requests.post(url, headers=self.teamup_headers, json=event_data)
            
            if response.status_code == 201:
                result = response.json()
                logger.info(f"✅ Created TeamUp event: {event_data.get('title', 'No title')}")
                return result
            else:
                logger.error(f"❌ Failed to create TeamUp event: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error creating TeamUp event: {str(e)}")
            return None
    
    def sync_events(self):
        """
        Sync events from Action Network to TeamUp
        """
        try:
            logger.info("🔄 Starting event sync...")
            
            # Fetch events from Action Network
            an_events = self.fetch_action_network_events()
            
            new_events_count = 0
            
            for an_event in an_events:
                event_id = an_event.get('identifier', an_event.get('id', ''))
                
                # Skip if we've already synced this event
                if event_id in synced_events:
                    continue
                
                # Transform and create in TeamUp
                teamup_event_data = self.transform_action_network_event(an_event)
                
                if teamup_event_data:
                    result = self.create_teamup_event(teamup_event_data)
                    
                    if result:
                        synced_events.add(event_id)
                        new_events_count += 1
                        logger.info(f"✅ Synced event: {teamup_event_data.get('title', 'No title')}")
                    else:
                        logger.error(f"❌ Failed to sync event: {teamup_event_data.get('title', 'No title')}")
            
            logger.info(f"🔄 Sync complete. {new_events_count} new events synced.")
            return new_events_count
            
        except Exception as e:
            logger.error(f"❌ Error during sync: {str(e)}")
            return 0
    
    def test_action_network_connection(self):
        """
        Test connection to Action Network API
        """
        try:
            url = f"{self.action_network_base_url}/events"
            params = {'limit': 1}
            
            response = requests.get(url, headers=self.action_network_headers, params=params)
            
            if response.status_code == 200:
                logger.info("✅ Action Network API connection successful")
                return True
            else:
                logger.error(f"❌ Action Network API connection failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error testing Action Network connection: {str(e)}")
            return False
    
    def test_teamup_connection(self):
        """
        Test connection to TeamUp API
        """
        try:
            url = f"{self.teamup_base_url}/events"
            response = requests.get(url, headers=self.teamup_headers, params={'limit': 1})
            
            if response.status_code == 200:
                logger.info("✅ TeamUp API connection successful")
                return True
            else:
                logger.error(f"❌ TeamUp API connection failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error testing TeamUp connection: {str(e)}")
            return False

# Initialize sync service
sync_service = ActionNetworkTeamUpSync()

def background_sync():
    """
    Background thread for periodic syncing
    """
    while True:
        try:
            # Wait 30 minutes between syncs
            time.sleep(30 * 60)  # 30 minutes
            sync_service.sync_events()
        except Exception as e:
            logger.error(f"❌ Background sync error: {str(e)}")

# Start background sync thread
if ACTION_NETWORK_API_KEY:
    sync_thread = threading.Thread(target=background_sync, daemon=True)
    sync_thread.start()
    logger.info("🔄 Background sync thread started (runs every 30 minutes)")

@app.route('/', methods=['GET'])
def home():
    """
    Home page - shows service status
    """
    return jsonify({
        'service': 'Action Network to TeamUp Integration',
        'status': 'running',
        'mode': 'API Polling',
        'sync_interval': '30 minutes',
        'endpoints': {
            'health': '/health',
            'sync_now': '/sync',
            'status': '/status'
        }
    })

@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint
    """
    teamup_configured = bool(TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY)
    action_network_configured = bool(ACTION_NETWORK_API_KEY)
    
    teamup_connection = False
    action_network_connection = False
    
    if teamup_configured:
        teamup_connection = sync_service.test_teamup_connection()
    
    if action_network_configured:
        action_network_connection = sync_service.test_action_network_connection()
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'teamup_api_configured': teamup_configured,
            'action_network_api_configured': action_network_configured,
            'teamup_connection_working': teamup_connection,
            'action_network_connection_working': action_network_connection
        },
        'sync_status': {
            'events_synced_count': len(synced_events),
            'background_sync_running': True if ACTION_NETWORK_API_KEY else False
        }
    })

@app.route('/sync', methods=['POST'])
def manual_sync():
    """
    Manually trigger a sync
    """
    try:
        logger.info("🔄 Manual sync triggered")
        new_events = sync_service.sync_events()
        
        return jsonify({
            'status': 'success',
            'message': f'Sync completed. {new_events} new events synced.',
            'new_events_count': new_events,
            'total_synced_events': len(synced_events)
        })
        
    except Exception as e:
        logger.error(f"❌ Manual sync error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/status', methods=['GET'])
def sync_status():
    """
    Get sync status
    """
    return jsonify({
        'total_events_synced': len(synced_events),
        'synced_event_ids': list(synced_events),
        'last_sync': 'Running in background every 30 minutes',
        'next_sync': 'Within 30 minutes'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    # Check configuration
    if not TEAMUP_API_KEY:
        logger.warning("⚠️  TeamUp API key not configured!")
    
    if not TEAMUP_CALENDAR_KEY:
        logger.warning("⚠️  TeamUp Calendar key not configured!")
    
    if not ACTION_NETWORK_API_KEY:
        logger.warning("⚠️  Action Network API key not configured!")
    
    logger.info(f"🚀 Starting Action Network to TeamUp Sync Service on port {port}")
    logger.info(f"📡 Manual sync endpoint: /sync")
    logger.info(f"❤️  Health check: /health")
    logger.info(f"📊 Status endpoint: /status")
    
    app.run(host='0.0.0.0', port=port, debug=False)